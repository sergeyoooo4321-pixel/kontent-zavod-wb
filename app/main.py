"""FastAPI entry point. Эндпоинты /healthz и /tg/webhook."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from fastapi import BackgroundTasks, FastAPI, Request

from .config import settings
from .internal_api import router as internal_router
from .kie_ai import KieAIClient
from .ozon import OzonClient
from .pipeline import Deps
from .s3 import S3Client
from .telegram import TelegramClient
from .tg_handler import handle_update
from .wb import WBClient

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
# Отключаем INFO-логирование URL'ов в httpx — там в URL может быть токен (Telegram).
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
# aiobotocore тоже шумит ключами в DEBUG
logging.getLogger("aiobotocore").setLevel(logging.WARNING)
logging.getLogger("botocore").setLevel(logging.WARNING)

logger = logging.getLogger("cz-backend")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Один общий httpx клиент на весь сервис
    timeout = httpx.Timeout(settings.HTTP_TIMEOUT_SEC, connect=10.0)
    http = httpx.AsyncClient(timeout=timeout, follow_redirects=True)

    tg = TelegramClient(settings.TG_BOT_TOKEN, http, settings.TG_API_BASE)
    # AI-провайдер: aitunnel.ru (имя класса историческое — раньше был kie.ai).
    kie = KieAIClient(
        base_url=settings.AITUNNEL_BASE,
        api_key=settings.AITUNNEL_API_KEY,
        http=http,
        image_model=settings.AITUNNEL_IMAGE_MODEL,
        llm_model=settings.AITUNNEL_LLM_MODEL,
        llm_fallback_model=settings.AITUNNEL_LLM_FALLBACK_MODEL,
        poll_interval=settings.AITUNNEL_POLL_INTERVAL_SEC,
        poll_max_attempts=settings.AITUNNEL_POLL_MAX_ATTEMPTS,
        max_concurrent=settings.AITUNNEL_MAX_CONCURRENT,
        rate_per_sec=settings.AITUNNEL_RATE_PER_SEC,
    )
    s3 = S3Client(
        endpoint=settings.S3_ENDPOINT,
        region=settings.S3_REGION,
        bucket=settings.S3_BUCKET,
        access_key=settings.S3_ACCESS_KEY,
        secret_key=settings.S3_SECRET_KEY,
        public_base=settings.S3_PUBLIC_BASE,
        http=http,
    )
    # Default-кабинет для backward-compat (используется когда RunRequest не указывает cabinet_names).
    # Берём первый настроенный кабинет; если кабинетов нет — фоллбэк на "пустые" клиенты,
    # они вернут осмысленную ошибку при попытке заливки.
    default_cab_name = settings.default_cabinet_name
    default_cab = settings.get_cabinet(default_cab_name) if default_cab_name else None
    ozon_cid = default_cab.ozon.client_id if default_cab and default_cab.has_ozon else (settings.OZON_CLIENT_ID or "")
    ozon_key = default_cab.ozon.api_key if default_cab and default_cab.has_ozon else (settings.OZON_API_KEY or "")
    wb_tok = default_cab.wb.token if default_cab and default_cab.has_wb else (settings.WB_TOKEN or "")

    ozon = OzonClient(base=settings.OZON_BASE, client_id=ozon_cid, api_key=ozon_key, http=http)
    wb = WBClient(base=settings.WB_BASE, token=wb_tok, http=http)

    await s3.start()  # долгоживущий aiobotocore-клиент

    app.state.deps = Deps(tg=tg, kie=kie, s3=s3, ozon=ozon, wb=wb, http=http)
    app.state.http = http
    logger.info("cz-backend started: ai=%s s3=%s ozon=%s wb=%s",
                settings.AITUNNEL_BASE, settings.S3_BUCKET,
                "yes" if settings.has_ozon_creds else "no",
                "yes" if settings.has_wb_creds else "no")
    try:
        yield
    finally:
        await http.aclose()
        await s3.aclose()
        logger.info("cz-backend stopped")


app = FastAPI(title="Контент завод backend", version="1.0.0", lifespan=lifespan)
app.include_router(internal_router)


@app.get("/healthz")
async def healthz():
    return {
        "status": "ok",
        "ozon_creds": settings.has_ozon_creds,
        "wb_creds": settings.has_wb_creds,
        "ts": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/tg/webhook", status_code=200)
async def tg_webhook(request: Request, bg: BackgroundTasks):
    """Прямой Telegram webhook handler. Заменяет n8n-приёмку.

    Telegram POSTит сюда update — мы парсим, обновляем session-state,
    отвечаем юзеру, при confirm запускаем pipeline в фоне.
    """
    try:
        update = await request.json()
    except Exception as e:
        logger.warning("tg/webhook bad json: %s", e)
        return {"ok": True}
    deps: Deps = app.state.deps
    bg.add_task(handle_update, update, deps)
    return {"ok": True}


