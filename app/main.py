"""FastAPI entry point. Эндпоинты /healthz и /api/run."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, status

from .config import settings
from .kie_ai import KieAIClient
from .models import RunRequest, RunResponse
from .ozon import OzonClient
from .pipeline import Deps, run_batch
from .s3 import S3Client
from .telegram import TelegramClient
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
    kie = KieAIClient(
        base_url=settings.KIE_BASE,
        api_key=settings.KIE_API_KEY,
        http=http,
        image_model=settings.KIE_IMAGE_MODEL,
        llm_model=settings.KIE_LLM_MODEL,
        poll_interval=settings.KIE_POLL_INTERVAL_SEC,
        poll_max_attempts=settings.KIE_POLL_MAX_ATTEMPTS,
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
    ozon = OzonClient(
        base=settings.OZON_BASE,
        client_id=settings.OZON_CLIENT_ID,
        api_key=settings.OZON_API_KEY,
        http=http,
    )
    wb = WBClient(base=settings.WB_BASE, token=settings.WB_TOKEN, http=http)

    app.state.deps = Deps(tg=tg, kie=kie, s3=s3, ozon=ozon, wb=wb)
    app.state.http = http
    logger.info("cz-backend started: kie=%s s3=%s ozon=%s wb=%s",
                settings.KIE_BASE, settings.S3_BUCKET,
                "yes" if settings.has_ozon_creds else "no",
                "yes" if settings.has_wb_creds else "no")
    try:
        yield
    finally:
        await http.aclose()
        await s3.aclose()
        logger.info("cz-backend stopped")


app = FastAPI(title="Контент завод backend", version="1.0.0", lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    return {
        "status": "ok",
        "ozon_creds": settings.has_ozon_creds,
        "wb_creds": settings.has_wb_creds,
        "ts": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/run", response_model=RunResponse, status_code=status.HTTP_202_ACCEPTED)
async def api_run(
    req: RunRequest,
    bg: BackgroundTasks,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
):
    if settings.INTERNAL_TOKEN and x_internal_token != settings.INTERNAL_TOKEN:
        raise HTTPException(status_code=403, detail="bad internal token")
    deps: Deps = app.state.deps
    bg.add_task(run_batch, req, deps)
    logger.info("api/run queued batch=%s products=%d", req.batch_id, len(req.products))
    return RunResponse(
        batch_id=req.batch_id,
        queued=True,
        received_at=datetime.now(timezone.utc).isoformat(),
    )
