"""Тесты TelegramClient (моки)."""
import httpx
import pytest

from app.telegram import TelegramClient, TelegramError


@pytest.mark.asyncio
async def test_send_ok(respx_mock):
    respx_mock.post("https://api.telegram.org/bot123/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
    )
    async with httpx.AsyncClient() as http:
        tg = TelegramClient("123", http)
        out = await tg.send(42, "hi")
        assert out["ok"] is True


@pytest.mark.asyncio
async def test_send_strips_long(respx_mock):
    respx_mock.post("https://api.telegram.org/bot123/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    async with httpx.AsyncClient() as http:
        tg = TelegramClient("123", http)
        await tg.send(42, "x" * 5000)


@pytest.mark.asyncio
async def test_get_file_path(respx_mock):
    respx_mock.get("https://api.telegram.org/bot123/getFile").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"file_path": "photos/1.jpg"}})
    )
    async with httpx.AsyncClient() as http:
        tg = TelegramClient("123", http)
        path = await tg.get_file_path("file_id_x")
        assert path == "photos/1.jpg"


@pytest.mark.asyncio
async def test_send_error(respx_mock):
    respx_mock.post("https://api.telegram.org/bot123/sendMessage").mock(
        return_value=httpx.Response(400, json={"ok": False, "description": "bad"})
    )
    async with httpx.AsyncClient() as http:
        tg = TelegramClient("123", http)
        with pytest.raises(TelegramError):
            await tg.send(42, "hi", parse_mode=None)
