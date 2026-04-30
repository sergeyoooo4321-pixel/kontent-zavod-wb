"""Telegram update handler с state machine (вместо n8n).

Принимает прямые webhook-обновления от Telegram и управляет UX:
  idle → photos → names → confirm → running

При переходе в running запускает pipeline.run_batch() в фоне.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .models import ProductIn, RunRequest

logger = logging.getLogger(__name__)


@dataclass
class TgSession:
    phase: str = "idle"
    photos: list[dict[str, Any]] = field(default_factory=list)  # [{file_id, idx}]
    products: list[dict[str, Any]] = field(default_factory=list)  # [{name, sku}]
    started_at: float = field(default_factory=time.time)


# Глобальное хранилище сессий per chat_id (in-memory, при рестарте — обнуляется)
_sessions: dict[int, TgSession] = {}


def _get_session(chat_id: int) -> TgSession:
    s = _sessions.get(chat_id)
    if s is None or (time.time() - s.started_at) > 86400:  # 24h cleanup
        s = TgSession()
        _sessions[chat_id] = s
    return s


# ── клавиатуры ───────────────────────────────────────────────────


def _kb_idle() -> dict:
    return {
        "keyboard": [
            [{"text": "🚀 Новая партия"}],
            [{"text": "📸 Этап 1: Фото"}, {"text": "📂 Этап 2: Категории"}],
            [{"text": "✏️ Этап 3: Карточки"}, {"text": "🚚 Этап 4: Заливка"}],
            [{"text": "📊 Статус"}, {"text": "ℹ️ Помощь"}],
        ],
        "resize_keyboard": True, "is_persistent": True,
    }


def _kb_photos() -> dict:
    return {
        "keyboard": [
            [{"text": "✅ Перейти к названиям"}],
            [{"text": "📊 Статус"}, {"text": "ℹ️ Помощь"}],
            [{"text": "🔄 Сбросить"}],
        ],
        "resize_keyboard": True, "is_persistent": True,
    }


def _kb_names() -> dict:
    return {
        "keyboard": [[{"text": "🔄 Сбросить"}, {"text": "ℹ️ Помощь"}]],
        "resize_keyboard": True, "is_persistent": True,
    }


def _kb_confirm() -> dict:
    return {
        "keyboard": [[{"text": "🚀 Генерация"}, {"text": "❌ Отмена"}]],
        "resize_keyboard": True, "is_persistent": True,
    }


def _kb_running() -> dict:
    return {
        "keyboard": [[{"text": "🔄 Сбросить"}, {"text": "📊 Статус"}]],
        "resize_keyboard": True, "is_persistent": True,
    }


def _kb_for(phase: str) -> dict:
    return {
        "idle": _kb_idle(),
        "photos": _kb_photos(),
        "names": _kb_names(),
        "confirm": _kb_confirm(),
        "running": _kb_running(),
    }.get(phase, _kb_idle())


# ── handlers ──────────────────────────────────────────────────────


async def _send(deps, chat_id: int, text: str, kb: dict | None = None) -> None:
    """Отправить сообщение пользователю с опциональной клавиатурой через bot API напрямую."""
    import httpx, json
    from .config import settings
    body = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    if kb is not None:
        body["reply_markup"] = kb
    url = f"{settings.TG_API_BASE}/bot{settings.TG_BOT_TOKEN}/sendMessage"
    try:
        r = await deps.tg._http.post(url, json=body, timeout=15)
        if r.status_code >= 400:
            # Markdown fallback
            body.pop("parse_mode", None)
            r = await deps.tg._http.post(url, json=body, timeout=15)
    except Exception as e:
        logger.warning("tg send fail: %s", e)


async def handle_update(update: dict, deps) -> None:
    """Главный обработчик одного Telegram update."""
    msg = update.get("message") or {}
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    if not chat_id:
        return

    text = (msg.get("text") or "").strip()
    photos = msg.get("photo") or []

    s = _get_session(chat_id)

    # ── universal commands ───────────────────────────────
    if text in ("/reset", "🔄 Сбросить", "❌ Отмена"):
        _sessions[chat_id] = TgSession()
        await _send(deps, chat_id, "🧹 *Сессия сброшена.* Жми «🚀 Новая партия».", _kb_idle())
        return
    if text in ("/help", "ℹ️ Помощь"):
        await _send(deps, chat_id,
            "*Помощь.*\n\n"
            "Сценарий:\n"
            "1) Жми «🚀 Новая партия»\n"
            "2) Кидай фото товаров — *по одному*\n"
            "3) Жми «✅ Перейти к названиям»\n"
            "4) Для каждого фото пиши `Название, артикул`\n"
            "5) Жми «🚀 Генерация» — я всё сделаю\n\n"
            "Команды: `/reset` `/status` `/help`",
            _kb_for(s.phase),
        )
        return
    if text in ("/status", "📊 Статус"):
        faza = {"idle": "_ожидание_", "photos": "*приём фото*", "names": "*приём названий*",
                "confirm": "*ожидает подтверждения*", "running": "*пайплайн идёт*"}.get(s.phase, s.phase)
        lines = [
            f"*Статус сессии:*",
            f"• Фаза: {faza}",
            f"• Фото принято: *{len(s.photos)}*",
            f"• Названий: *{len(s.products)}*",
        ]
        if s.products:
            lines.append("\nСписок:")
            for i, p in enumerate(s.products, 1):
                lines.append(f"{i}. {p['name']} `{p['sku']}`")
        await _send(deps, chat_id, "\n".join(lines), _kb_for(s.phase))
        return

    # ── info-buttons ─────────────────────────────────────
    info = {
        "📸 Этап 1: Фото": "📸 *Этап 1.* 4 фото 3:4 на товар через kie.ai (главное / набор 2 / набор 3 / доп). Доп.фото идёт во все 3 SKU.",
        "📂 Этап 2: Категории": "📂 *Этап 2.* Подбор категории Ozon+WB через LLM gpt-5-2 + скачивание Excel-шаблона + считывание справочников.",
        "✏️ Этап 3: Карточки": "✏️ *Этап 3.* Расширение до 3 SKU (одиночка/x2/x3), правила §5.2 ТЗ — габариты, веса, лимиты, мультивыбор `;`.",
        "🚚 Этап 4: Заливка": "🚚 *Этап 4.* `POST /v3/product/import` Ozon + `POST /content/v2/cards/upload` WB. Ошибка одного SKU не валит партию.",
    }
    if text in info:
        await _send(deps, chat_id, info[text], _kb_for(s.phase))
        return

    # ── state machine ────────────────────────────────────

    # idle → photos
    if s.phase == "idle":
        if text in ("/start", "🚀 Новая партия"):
            s.phase = "photos"
            s.photos = []
            s.products = []
            s.started_at = time.time()
            await _send(deps, chat_id,
                "👋 *Контент-завод. Новая партия.*\n\n"
                "Кидай фото товаров *по одному*. После каждого скажу что принял.\n"
                "Когда все фото — жми «✅ Перейти к названиям».",
                _kb_photos(),
            )
            return
        # любое другое в idle — подсказка
        await _send(deps, chat_id, "Жми «🚀 Новая партия» для запуска.", _kb_idle())
        return

    # photos
    if s.phase == "photos":
        if photos:
            biggest = photos[-1]
            s.photos.append({"file_id": biggest["file_id"], "idx": len(s.photos)})
            await _send(deps, chat_id,
                f"📷 *Фото {len(s.photos)} принято.*\n\nПришли следующее или жми «✅ Перейти к названиям».",
                _kb_photos(),
            )
            return
        if text == "✅ Перейти к названиям":
            if not s.photos:
                await _send(deps, chat_id, "Сначала пришли хотя бы одно фото.", _kb_photos())
                return
            if len(s.photos) > 10:
                await _send(deps, chat_id, f"Максимум 10 товаров. У тебя {len(s.photos)}. Жми «🔄 Сбросить».", _kb_photos())
                return
            s.phase = "names"
            await _send(deps, chat_id,
                f"✅ *Принято {len(s.photos)} фото.*\n\n"
                f"Теперь название и артикул для *фото №1*.\nФормат: `Название, артикул`",
                _kb_names(),
            )
            return
        if text:
            await _send(deps, chat_id, "Сейчас фаза приёма фото. Пришли фотографию или жми «✅ Перейти к названиям».", _kb_photos())
        return

    # names
    if s.phase == "names":
        if photos:
            await _send(deps, chat_id, "Сейчас фаза приёма названий. Фото добавлять нельзя.", _kb_names())
            return
        if not text:
            await _send(deps, chat_id, "Жду название+артикул.", _kb_names())
            return
        # парсим
        parts = [x.strip() for x in text.replace(";", ",").replace("\t", ",").split(",") if x.strip()]
        if len(parts) < 2:
            await _send(deps, chat_id,
                "Неверный формат. Должно быть: `Название, артикул`.\nПример: `Кофе зерновой Арабика 250г, COF-001`",
                _kb_names(),
            )
            return
        name = ", ".join(parts[:-1])
        sku = parts[-1]
        s.products.append({"name": name, "sku": sku})
        next_idx = len(s.products)
        if next_idx < len(s.photos):
            await _send(deps, chat_id,
                f"✔️ *Фото {next_idx}:* {name} `{sku}`\n\n"
                f"Теперь название+артикул для *фото №{next_idx + 1}*:",
                _kb_names(),
            )
            return
        # все названия введены — confirm
        s.phase = "confirm"
        lines = ["📝 *Партия готова к генерации:*", ""]
        for i, p in enumerate(s.products, 1):
            lines.append(f"{i}) {p['name']} `{p['sku']}`")
        lines.append("")
        lines.append(f"Всего: *{len(s.products)} товаров* × 4 фото = *{len(s.products) * 4} изображений*")
        lines.append("")
        lines.append("Жми «🚀 Генерация» или «❌ Отмена».")
        await _send(deps, chat_id, "\n".join(lines), _kb_confirm())
        return

    # confirm
    if s.phase == "confirm":
        if text == "🚀 Генерация":
            s.phase = "running"
            batch_id = f"{int(time.time())}-{uuid.uuid4().hex[:6]}"
            products = [
                ProductIn(idx=i, sku=p["sku"], name=p["name"], tg_file_id=s.photos[i]["file_id"])
                for i, p in enumerate(s.products)
            ]
            req = RunRequest(batch_id=batch_id, chat_id=chat_id, products=products)
            await _send(deps, chat_id,
                f"🚀 *Запускаю партию* `{batch_id}`. Прогресс пришлю отдельными сообщениями.",
                _kb_running(),
            )
            # запуск в фоне
            from .pipeline import run_batch
            asyncio.create_task(_run_and_cleanup(req, deps, chat_id))
            return
        if text == "❌ Отмена":
            _sessions[chat_id] = TgSession()
            await _send(deps, chat_id, "❌ Отменено. Жми «🚀 Новая партия» для нового запуска.", _kb_idle())
            return
        await _send(deps, chat_id, "Жми «🚀 Генерация» или «❌ Отмена».", _kb_confirm())
        return

    # running
    if s.phase == "running":
        await _send(deps, chat_id,
            "⏳ Пайплайн уже выполняется. Жди отчёт. «🔄 Сбросить» — сбросит сессию (но не остановит уже запущенный пайплайн).",
            _kb_running(),
        )
        return


async def _run_and_cleanup(req: RunRequest, deps, chat_id: int) -> None:
    from .pipeline import run_batch
    try:
        await run_batch(req, deps)
    finally:
        # после завершения возвращаем сессию в idle
        s = _sessions.get(chat_id)
        if s and s.phase == "running":
            _sessions[chat_id] = TgSession()
