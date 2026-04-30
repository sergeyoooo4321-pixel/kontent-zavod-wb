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
