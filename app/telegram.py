"""Telegram Bot API клиент: sendMessage, getFile, downloadFile.

Все исключения httpx маскируют токен в URL перед re-raise / логированием.
"""
from __future__ import annotations

import logging
import re

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)


_TOKEN_RE = re.compile(r"bot\d+:[A-Za-z0-9_\-]+")


def _mask_token(text: str) -> str:
    """Маскирует TG bot токен в любой строке (URL, error message)."""
    return _TOKEN_RE.sub("bot<TOKEN>", text or "")


class TelegramError(Exception):
    pass


class TelegramClient:
    def __init__(self, token: str, http: httpx.AsyncClient, api_base: str = "https://api.telegram.org"):
        self._token = token
        self._http = http
        self._api_base = api_base.rstrip("/")

    @property
    def _url(self) -> str:
        return f"{self._api_base}/bot{self._token}"

    @retry(
        retry=retry_if_exception_type((httpx.NetworkError, httpx.ReadTimeout, httpx.WriteTimeout)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=10),
        reraise=True,
    )
    async def send(
        self,
        chat_id: int,
        text: str,
        parse_mode: str | None = "Markdown",
        disable_web_page_preview: bool = True,
    ) -> dict:
        """Отправить текстовое сообщение. Безопасно — не падает на длинных сообщениях, режет до 4096."""
        if len(text) > 4096:
            text = text[:4090] + "…"
        body: dict = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if parse_mode:
            body["parse_mode"] = parse_mode
        r = await self._http.post(f"{self._url}/sendMessage", json=body)
        if r.status_code >= 400:
            # Если Markdown-парсинг сломался — повторим без parse_mode
            if parse_mode == "Markdown" and "parse" in r.text.lower():
                body.pop("parse_mode", None)
                r = await self._http.post(f"{self._url}/sendMessage", json=body)
        if r.status_code >= 400:
            raise TelegramError(f"sendMessage failed: {r.status_code} {r.text[:300]}")
        logger.info("tg.send chat_id=%s len=%d", chat_id, len(text))
        return r.json()

    @retry(
        retry=retry_if_exception_type((httpx.NetworkError, httpx.ReadTimeout)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=10),
        reraise=True,
    )
    async def get_file_path(self, file_id: str) -> str:
        """Получить file_path для последующего скачивания."""
        try:
            r = await self._http.get(f"{self._url}/getFile", params={"file_id": file_id})
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            # Маскируем токен в сообщении ошибки
            raise TelegramError(f"getFile {e.response.status_code}: file_id={file_id[:30]}...") from None
        data = r.json()
        if not data.get("ok"):
            raise TelegramError(f"getFile failed: {data}")
        return data["result"]["file_path"]

    @retry(
        retry=retry_if_exception_type((httpx.NetworkError, httpx.ReadTimeout)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=10),
        reraise=True,
    )
    async def download_file(self, file_path: str) -> bytes:
        """Скачать содержимое файла по file_path (живёт ~1 час)."""
        url = f"{self._api_base}/file/bot{self._token}/{file_path}"
        try:
            r = await self._http.get(url)
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise TelegramError(f"downloadFile {e.response.status_code}: path={file_path[:50]}") from None
        return r.content

    async def get_file_bytes(self, file_id: str) -> bytes:
        """Удобный шорткат: file_id → bytes."""
        path = await self.get_file_path(file_id)
        return await self.download_file(path)

    @retry(
        retry=retry_if_exception_type((httpx.NetworkError, httpx.ReadTimeout)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=10),
        reraise=True,
    )
    async def send_media_group(
        self,
        chat_id: int,
        photos: list[tuple[str, str | None]],  # [(url, caption?), ...]
    ) -> None:
        """Отправить альбом фото (до 10 штук) одним сообщением.

        photos: список кортежей (url, caption). Caption ставится только на ПЕРВОМ
        элементе альбома (Telegram показывает его под всем альбомом).
        """
        if not photos:
            return
        media = []
        for i, (url, caption) in enumerate(photos[:10]):
            item = {"type": "photo", "media": url}
            if i == 0 and caption:
                item["caption"] = caption[:1024]  # лимит
                item["parse_mode"] = "Markdown"
            media.append(item)
        body = {"chat_id": chat_id, "media": media}
        try:
            r = await self._http.post(f"{self._url}/sendMediaGroup", json=body)
            if r.status_code >= 400:
                # Если Markdown сломался — повторим без parse_mode
                if media[0].get("parse_mode"):
                    media[0].pop("parse_mode", None)
                    r = await self._http.post(f"{self._url}/sendMediaGroup", json=body)
        except httpx.HTTPStatusError as e:
            raise TelegramError(f"sendMediaGroup {e.response.status_code}") from None
        if r.status_code >= 400:
            logger.warning("sendMediaGroup %s: %s", r.status_code, r.text[:200])
            # Фолбэк: отправить как простые ссылки текстом
            text = "\n".join(f"• {u}" for u, _ in photos)
            await self.send(chat_id, text, parse_mode=None)
            return
        logger.info("tg.media_group chat_id=%s count=%d", chat_id, len(media))

    async def send_with_buttons(
        self,
        chat_id: int,
        text: str,
        buttons: list[list[dict]],
        parse_mode: str | None = "Markdown",
    ) -> dict | None:
        """Отправить сообщение с inline-кнопками.

        buttons — двумерный массив рядов; элемент = {"text": ..., "callback_data": ...}.
        Возвращает result (содержит message_id) для последующего edit_message_text.
        """
        if len(text) > 4096:
            text = text[:4090] + "…"
        body: dict = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
            "reply_markup": {"inline_keyboard": buttons},
        }
        if parse_mode:
            body["parse_mode"] = parse_mode
        r = await self._http.post(f"{self._url}/sendMessage", json=body)
        if r.status_code >= 400 and parse_mode == "Markdown":
            body.pop("parse_mode", None)
            r = await self._http.post(f"{self._url}/sendMessage", json=body)
        if r.status_code >= 400:
            logger.warning("send_with_buttons %s: %s", r.status_code, r.text[:200])
            return None
        logger.info("tg.send_with_buttons chat_id=%s buttons=%d", chat_id,
                    sum(len(row) for row in buttons))
        return r.json().get("result")

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        buttons: list[list[dict]] | None = None,
        parse_mode: str | None = "Markdown",
    ) -> bool:
        """Редактировать ранее отправленное сообщение.

        Используется для кнопок-меню — нажал, текст и клавиатура обновились
        в том же сообщении (а не плодим новые).
        """
        if len(text) > 4096:
            text = text[:4090] + "…"
        body: dict = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if buttons is not None:
            body["reply_markup"] = {"inline_keyboard": buttons}
        if parse_mode:
            body["parse_mode"] = parse_mode
        r = await self._http.post(f"{self._url}/editMessageText", json=body)
        if r.status_code >= 400 and parse_mode == "Markdown":
            body.pop("parse_mode", None)
            r = await self._http.post(f"{self._url}/editMessageText", json=body)
        if r.status_code >= 400:
            # 400 message not modified — нормально, не шумим
            if "message is not modified" not in r.text.lower():
                logger.warning("edit_message_text %s: %s", r.status_code, r.text[:200])
            return False
        return True

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> None:
        """Закрыть «крутилку» на нажатой кнопке. Опционально показать toast."""
        body: dict = {"callback_query_id": callback_query_id}
        if text:
            body["text"] = text[:200]
        if show_alert:
            body["show_alert"] = True
        try:
            await self._http.post(f"{self._url}/answerCallbackQuery", json=body, timeout=5)
        except Exception as e:
            logger.warning("answer_callback %s: %s", callback_query_id, e)

    @retry(
        retry=retry_if_exception_type((httpx.NetworkError, httpx.ReadTimeout, httpx.WriteTimeout)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=10),
        reraise=True,
    )
    async def send_document(
        self,
        chat_id: int,
        content: bytes,
        filename: str,
        caption: str | None = None,
        parse_mode: str | None = "Markdown",
    ) -> dict:
        """Отправить файл как документ (multipart). Лимит Telegram: 50 МБ.

        Используется для ZIP-архивов с фотками.
        """
        files = {"document": (filename, content, "application/zip")}
        data: dict = {"chat_id": str(chat_id)}
        if caption:
            data["caption"] = caption[:1024]
            if parse_mode:
                data["parse_mode"] = parse_mode
        try:
            r = await self._http.post(
                f"{self._url}/sendDocument",
                data=data, files=files, timeout=120.0,
            )
            if r.status_code >= 400 and parse_mode:
                # Markdown сломался — повторим без него
                data.pop("parse_mode", None)
                r = await self._http.post(
                    f"{self._url}/sendDocument",
                    data=data, files=files, timeout=120.0,
                )
        except httpx.HTTPStatusError as e:
            raise TelegramError(f"sendDocument {e.response.status_code}") from None
        if r.status_code >= 400:
            raise TelegramError(f"sendDocument failed: {r.status_code} {r.text[:200]}")
        logger.info("tg.send_document chat_id=%s file=%s size=%d", chat_id, filename, len(content))
        return r.json()
