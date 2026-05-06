"""FastAPI entry point гномика. /healthz, /chat, /sessions, /reload-memory."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .agent import QueryEngine
from .config import settings
from .llm import KieLLM
from .sessions import SessionStore
from .tools import ToolRegistry

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("gnome")


class ChatIn(BaseModel):
    chat_id: int
    text: str = ""
    images: list[str] = []  # публичные URL картинок (S3 / любой http)


class ChatOut(BaseModel):
    chat_id: int
    reply: str
    approval_required: bool = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    timeout = httpx.Timeout(180.0, connect=10.0)
    http = httpx.AsyncClient(timeout=timeout)
    # aitunnel.ru: один endpoint, ключ начинается на sk-aitunnel-
    base = settings.AITUNNEL_BASE or settings.KIE_BASE
    api_key = settings.AITUNNEL_API_KEY or settings.KIE_API_KEY
    llm = KieLLM(base=base, api_key=api_key, http=http)
    registry = ToolRegistry(skills_dir=settings.skills_dir)
    sessions = SessionStore(db_path=settings.data_dir / "sessions.db")
    engine = QueryEngine(settings=settings, llm=llm, registry=registry, sessions=sessions)

    app.state.http = http
    app.state.llm = llm
    app.state.registry = registry
    app.state.sessions = sessions
    app.state.engine = engine

    logger.info("gnome up: model=%s tools=%d port=%d",
                settings.LLM_MODEL, len(registry.all()), settings.AGENT_PORT)
    try:
        yield
    finally:
        await http.aclose()
        logger.info("gnome down")


app = FastAPI(title="Гномик контент-завода", version="0.1.0", lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    registry: ToolRegistry = app.state.registry
    return {
        "status": "ok",
        "model": settings.LLM_MODEL,
        "tools": [t.name for t in registry.all()],
        "ts": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/chat", response_model=ChatOut)
async def chat(req: ChatIn):
    if not settings.KIE_API_KEY:
        raise HTTPException(status_code=503, detail="KIE_API_KEY не задан в .env")
    engine: QueryEngine = app.state.engine
    reply = await engine.query(req.chat_id, req.text, images=req.images)
    from .agent import APPROVAL_MARKER
    approval = APPROVAL_MARKER in reply
    if approval:
        reply = reply.replace(APPROVAL_MARKER, "").strip()
    return ChatOut(chat_id=req.chat_id, reply=reply, approval_required=approval)


@app.get("/sessions")
async def sessions_list():
    sessions: SessionStore = app.state.sessions
    return {"chat_ids": sessions.list_chats()}


@app.post("/sessions/{chat_id}/reset")
async def session_reset(chat_id: int):
    sessions: SessionStore = app.state.sessions
    sessions.reset(chat_id)
    return {"ok": True, "chat_id": chat_id}


@app.post("/reload-memory")
async def reload_memory():
    """Перечитать CLAUDE.md и memory/*.md без рестарта (если правил файлы)."""
    engine: QueryEngine = app.state.engine
    engine.reload_memory()
    return {"ok": True}
