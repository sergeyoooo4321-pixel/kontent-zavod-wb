from __future__ import annotations

import html
import json
from typing import Any

import httpx

from app.config import mask_secret


class TelegramClient:
    def __init__(self, token: str, api_base: str, timeout: int = 90):
        self.token = token
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout

    @property
    def base_url(self) -> str:
        return f"{self.api_base}/bot{self.token}"

    async def request(self, method: str, payload: dict[str, Any] | None = None, files: dict[str, Any] | None = None) -> Any:
        if not self.token:
            raise RuntimeError("TG_BOT_TOKEN is not configured")
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            if files:
                response = await client.post(f"{self.base_url}/{method}", data=payload or {}, files=files)
            else:
                response = await client.post(f"{self.base_url}/{method}", json=payload or {})
        if response.status_code >= 400:
            text = response.text.replace(self.token, mask_secret(self.token))
            raise RuntimeError(f"Telegram {method} failed: HTTP {response.status_code}: {text[:500]}")
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {data}")
        return data.get("result")

    async def send_message(self, chat_id: int, text: str, reply_markup: dict[str, Any] | None = None) -> Any:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text[:3900], "disable_web_page_preview": True}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return await self.request("sendMessage", payload)

    async def send_document(self, chat_id: int, content: bytes, filename: str, caption: str = "") -> Any:
        return await self.request(
            "sendDocument",
            {"chat_id": chat_id, "caption": caption[:1000]},
            files={"document": (filename, content, "application/zip")},
        )

    async def get_file_bytes(self, file_id: str) -> tuple[bytes, str]:
        file_info = await self.request("getFile", {"file_id": file_id})
        file_path = file_info["file_path"]
        url = f"{self.api_base}/file/bot{self.token}/{file_path}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(url)
        response.raise_for_status()
        return response.content, file_path


def keyboard(*rows: list[str]) -> dict[str, Any]:
    return {
        "keyboard": [[{"text": item} for item in row] for row in rows],
        "resize_keyboard": True,
        "one_time_keyboard": False,
    }


def escape(value: str) -> str:
    return html.escape(value, quote=False)


def json_keyboard(buttons: list[list[str]]) -> str:
    return json.dumps(keyboard(*buttons), ensure_ascii=False)

