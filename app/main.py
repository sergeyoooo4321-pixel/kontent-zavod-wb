from __future__ import annotations

import logging

from fastapi import FastAPI, Header, HTTPException, Request

from app.bot import Bot
from app.config import settings
from app.db import StateStore
from app.image_ai import ImageGenerator
from app.pipeline import BatchProcessor
from app.storage import Storage
from app.telegram import TelegramClient


logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))

settings.RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
settings.MEDIA_FALLBACK_DIR.mkdir(parents=True, exist_ok=True)

store = StateStore(settings.SQLITE_PATH)
telegram = TelegramClient(settings.TG_BOT_TOKEN, settings.TG_API_BASE, settings.HTTP_TIMEOUT_SEC)
storage = Storage(settings)
image_generator = ImageGenerator(settings)
processor = BatchProcessor(settings, telegram, storage, image_generator)
bot = Bot(store, telegram, processor, settings.MAX_PHOTOS_PER_BATCH)

app = FastAPI(title="Content Zavod", version="2.0.0")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"ok": "true"}


@app.post("/tg/webhook")
async def telegram_webhook(request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)) -> dict[str, bool]:
    if settings.TG_WEBHOOK_SECRET_TOKEN and x_telegram_bot_api_secret_token != settings.TG_WEBHOOK_SECRET_TOKEN:
        raise HTTPException(status_code=403, detail="bad secret token")
    update = await request.json()
    await bot.handle_update(update)
    return {"ok": True}

