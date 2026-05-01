"""Telegram update handler с inline-меню и multi-cabinet UX.

Главное меню (`/start` → одно inline-сообщение, которое редактируется):
  🏪 Кабинет: Профит ▾
  📦 Новая партия товаров
  ⚙️ Настройки (DRY_RUN: вкл)
  ℹ️ Помощь

Callback flow:
  menu:main         — вернуться в главное меню
  cab:<name>        — выбрать кабинет (профит/прогресс24/...)
  cab:all           — mirror-режим (все кабинеты)
  flow:start        — старт новой партии (выбор кабинета → photos)
  flow:run          — запустить генерацию (после загрузки фото и названий)
  flow:cancel       — отменить и вернуться в главное меню
  mode:dry_on/off   — переключить DRY_RUN
  help              — показать справку

Состояния партии: idle → photos → names → confirm → running.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .config import CABINET_LABELS, settings
from .models import ProductIn, RunRequest

logger = logging.getLogger(__name__)


# ─── per-chat session ─────────────────────────────────────────────


@dataclass
class TgSession:
    phase: str = "idle"
    photos: list[dict[str, Any]] = field(default_factory=list)
    products: list[dict[str, Any]] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)

    # multi-cabinet UX:
    cabinet: str | None = None  # "profit"|"progress24"|"progress247"|"tnp"|"default"|"all"
    menu_message_id: int | None = None  # сообщение главного меню (для edit)


_sessions: dict[int, TgSession] = {}


def _get_session(chat_id: int) -> TgSession:
    s = _sessions.get(chat_id)
    if s is None or (time.time() - s.started_at) > 86400:
        s = TgSession()
        _sessions[chat_id] = s
    return s


def _reset_session(chat_id: int, keep_cabinet: bool = True) -> TgSession:
    """Сбрасывает партию, сохраняя выбранный кабинет."""
    cab = _sessions.get(chat_id).cabinet if (_sessions.get(chat_id) and keep_cabinet) else None
    s = TgSession(cabinet=cab)
    _sessions[chat_id] = s
    return s


# ─── helpers для текстов и кнопок ─────────────────────────────────


def _cabinet_label(name: str | None) -> str:
    if name == "all":
        return "🔄 Все сразу"
    if not name:
        return "не выбран"
    return CABINET_LABELS.get(name, name)


def _main_menu_text(s: TgSession) -> str:
    cab = _cabinet_label(s.cabinet)
    dry = "✅ вкл" if settings.DRY_RUN else "❌ выкл"
    return (
        "🏭 *Контент-завод*\n\n"
        f"🏪 Кабинет: *{cab}*\n"
        f"⚙️ DRY\\_RUN: {dry}\n\n"
        "_DRY\\_RUN — заглушка от заливки. Когда вкл — карточки на МП НЕ публикуются, "
        "а в чат приходит JSON с тем что бы ушло._"
    )


def _main_menu_buttons(s: TgSession) -> list[list[dict]]:
    cab = _cabinet_label(s.cabinet)
    dry = "✅ вкл" if settings.DRY_RUN else "❌ выкл"
    return [
        [{"text": f"🏪 Кабинет: {cab}", "callback_data": "menu:cabinets"}],
        [{"text": "📦 Новая партия товаров", "callback_data": "flow:start"}],
        [{"text": f"⚙️ DRY_RUN: {dry}", "callback_data": "mode:dry_toggle"}],
        [{"text": "ℹ️ Помощь", "callback_data": "help"}],
    ]


def _cabinet_menu_buttons() -> list[list[dict]]:
    """Список доступных кабинетов из settings + Mirror-кнопка."""
    rows: list[list[dict]] = []
    cabs = settings.list_cabinets()
    # По 2 кабинета в ряду
    pair: list[dict] = []
    for c in cabs:
        marks = []
        if c.has_ozon:
            marks.append("О")
        if c.has_wb:
            marks.append("В")
        suffix = f" ({'+'.join(marks)})" if marks else ""
        pair.append({"text": f"{c.label}{suffix}", "callback_data": f"cab:{c.name}"})
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    if cabs:
        rows.append([{"text": "🔄 Все сразу (mirror)", "callback_data": "cab:all"}])
    rows.append([{"text": "← Назад", "callback_data": "menu:main"}])
    return rows


def _help_text() -> str:
    return (
        "*Как пользоваться:*\n\n"
        "1. Жмёшь *📦 Новая партия*\n"
        "2. Если кабинет ещё не выбран — выбираешь (Профит / Прогресс 24 / ТНП / Прогресс 247 / 🔄 Все сразу)\n"
        "3. Кидаешь фото товаров — *по одному сообщению*\n"
        "4. Жмёшь *✅ Готово*\n"
        "5. Для каждого фото пишешь `Название, артикул`\n"
        "6. Подтверждаешь и я генерирую 4 фото на товар + создаю карточки на МП\n\n"
        "*Кабинеты:* О = Ozon настроен, В = WB настроен.\n"
        "*Mirror:* «🔄 Все сразу» — карточка появится во всех кабинетах одним прогоном.\n"
        "*DRY\\_RUN вкл:* всё проходит до Этапа 5, но в МП ничего не отправляется — "
        "получаешь JSON-payload в чат для проверки.\n\n"
        "Команды: `/reset` `/start`"
    )


# ─── reply-клавиатуры (для приёма фото и названий, более привычно) ─


def _kb_photos() -> dict:
    return {
        "keyboard": [
            [{"text": "✅ Готово, к названиям"}],
            [{"text": "❌ Отмена"}],
        ],
        "resize_keyboard": True, "is_persistent": True,
    }


def _kb_names() -> dict:
    return {
        "keyboard": [[{"text": "❌ Отмена"}]],
        "resize_keyboard": True, "is_persistent": True,
    }


def _kb_remove() -> dict:
    """Скрыть reply-клавиатуру, оставить inline-меню."""
    return {"remove_keyboard": True}


# ─── send / edit helpers ──────────────────────────────────────────


async def _send(deps, chat_id: int, text: str, kb: dict | None = None,
                parse_mode: str | None = "Markdown") -> dict | None:
    """Простая отправка через TelegramClient.send, опционально reply-клавиатура."""
    import httpx
    body: dict = {
        "chat_id": chat_id,
        "text": text[:4090],
        "disable_web_page_preview": True,
    }
    if parse_mode:
        body["parse_mode"] = parse_mode
    if kb is not None:
        body["reply_markup"] = kb
    url = f"{settings.TG_API_BASE}/bot{settings.TG_BOT_TOKEN}/sendMessage"
    try:
        r = await deps.tg._http.post(url, json=body, timeout=15)
        if r.status_code >= 400 and parse_mode:
            body.pop("parse_mode", None)
            r = await deps.tg._http.post(url, json=body, timeout=15)
        if r.status_code >= 400:
            logger.warning("tg send fail %s: %s", r.status_code, r.text[:200])
            return None
        return r.json().get("result")
    except Exception as e:
        logger.warning("tg send fail: %s", e)
        return None


async def _send_main_menu(deps, chat_id: int, s: TgSession) -> None:
    """Отправляет (или редактирует, если есть menu_message_id) главное меню."""
    text = _main_menu_text(s)
    buttons = _main_menu_buttons(s)
    if s.menu_message_id:
        ok = await deps.tg.edit_message_text(chat_id, s.menu_message_id, text, buttons=buttons)
        if ok:
            return
        # сообщение нельзя редактировать (>48ч / удалили) — отправим новое
    res = await deps.tg.send_with_buttons(chat_id, text, buttons)
    if res and res.get("message_id"):
        s.menu_message_id = res["message_id"]


# ─── главный обработчик update ─────────────────────────────────────


async def handle_update(update: dict, deps) -> None:
    """Главный обработчик. Различает message и callback_query."""
    if "callback_query" in update:
        await _handle_callback(update["callback_query"], deps)
        return
    if "message" in update:
        await _handle_message(update["message"], deps)
        return


# ─── callback_query handler ───────────────────────────────────────


async def _handle_callback(cq: dict, deps) -> None:
    cq_id = cq.get("id") or ""
    data = cq.get("data") or ""
    msg = cq.get("message") or {}
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    msg_id = msg.get("message_id")
    if not chat_id or not data:
        await deps.tg.answer_callback_query(cq_id)
        return

    s = _get_session(chat_id)
    s.menu_message_id = msg_id  # запоминаем для последующего edit

    # Закрываем «крутилку» сразу же
    await deps.tg.answer_callback_query(cq_id)

    if data == "menu:main":
        await _send_main_menu(deps, chat_id, s)
        return

    if data == "menu:cabinets":
        cabs = settings.list_cabinets()
        if not cabs:
            text = ("⚠️ *Кабинеты не настроены.*\n\nДобавь токены Ozon/WB в `.env` "
                    "(`OZON_PROFIT_CLIENT_ID/OZON_PROFIT_API_KEY`, `WB_PROFIT_TOKEN`, и т.д.).")
            await deps.tg.edit_message_text(chat_id, msg_id, text,
                                             buttons=[[{"text": "← Назад", "callback_data": "menu:main"}]])
            return
        text = (
            "🏪 *Выбери кабинет.*\n\n"
            "О = Ozon настроен, В = WB настроен.\n"
            "*🔄 Все сразу* — карточка появится во всех кабинетах одним прогоном."
        )
        await deps.tg.edit_message_text(chat_id, msg_id, text, buttons=_cabinet_menu_buttons())
        return

    if data.startswith("cab:"):
        cab_name = data.split(":", 1)[1]
        if cab_name == "all":
            s.cabinet = "all"
        else:
            cab = settings.get_cabinet(cab_name)
            if not cab:
                await deps.tg.answer_callback_query(cq_id, "Кабинет не найден", show_alert=True)
                return
            s.cabinet = cab_name
        await _send_main_menu(deps, chat_id, s)
        return

    if data == "mode:dry_toggle":
        settings.DRY_RUN = not settings.DRY_RUN
        logger.info("DRY_RUN toggled to %s by chat=%s", settings.DRY_RUN, chat_id)
        await _send_main_menu(deps, chat_id, s)
        return

    if data == "help":
        await deps.tg.edit_message_text(
            chat_id, msg_id, _help_text(),
            buttons=[[{"text": "← Назад", "callback_data": "menu:main"}]],
        )
        return

    if data == "flow:start":
        if not s.cabinet:
            # сначала кабинет
            cabs = settings.list_cabinets()
            if not cabs:
                await deps.tg.answer_callback_query(cq_id, "Сначала настрой кабинеты в .env", show_alert=True)
                return
            text = (
                "🏪 *Выбери кабинет для партии.*\n\n"
                "О = Ozon, В = WB.\n*🔄 Все сразу* — во все настроенные кабинеты."
            )
            await deps.tg.edit_message_text(chat_id, msg_id, text, buttons=_cabinet_menu_buttons())
            return
        # перейти в фазу photos
        s.phase = "photos"
        s.photos = []
        s.products = []
        s.started_at = time.time()
        cab = _cabinet_label(s.cabinet)
        await deps.tg.edit_message_text(
            chat_id, msg_id,
            f"📦 *Новая партия* → кабинет *{cab}*\n\n"
            "Кидай фото товаров *по одному сообщению*.\nКогда все — жми «✅ Готово, к названиям».",
            buttons=[[{"text": "✖ Отмена", "callback_data": "flow:cancel"}]],
        )
        # reply-клавиатура поверх (для удобной кнопки «Готово»)
        await _send(deps, chat_id, "Жду фото 📷", kb=_kb_photos())
        return

    if data == "flow:cancel":
        _reset_session(chat_id, keep_cabinet=True)
        s = _get_session(chat_id)
        s.menu_message_id = msg_id
        await _send(deps, chat_id, "❌ Партия отменена.", kb=_kb_remove())
        await _send_main_menu(deps, chat_id, s)
        return

    if data == "flow:run":
        # подтверждение — запускаем pipeline
        if s.phase != "confirm":
            await deps.tg.answer_callback_query(cq_id, "Нечего запускать", show_alert=True)
            return
        await _start_pipeline(chat_id, deps, s)
        return


# ─── message handler (фото / текст) ───────────────────────────────


async def _handle_message(msg: dict, deps) -> None:
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    if not chat_id:
        return

    text = (msg.get("text") or "").strip()
    photos = msg.get("photo") or []
    s = _get_session(chat_id)

    # ── universal commands ───────────────────────────────
    if text in ("/reset", "🔄 Сбросить", "❌ Отмена"):
        _reset_session(chat_id, keep_cabinet=True)
        s = _get_session(chat_id)
        await _send(deps, chat_id, "🧹 Сессия сброшена.", kb=_kb_remove())
        await _send_main_menu(deps, chat_id, s)
        return

    if text in ("/start",):
        # Полный сброс ID сообщения меню чтобы старое не мешало
        s.menu_message_id = None
        await _send(deps, chat_id, "Загружаю меню…", kb=_kb_remove())
        await _send_main_menu(deps, chat_id, s)
        return

    if text in ("/help", "ℹ️ Помощь"):
        await _send(deps, chat_id, _help_text())
        return

    # ── state machine ────────────────────────────────────

    if s.phase == "idle":
        # любое сообщение в idle — показать меню
        await _send_main_menu(deps, chat_id, s)
        return

    if s.phase == "photos":
        if photos:
            biggest = photos[-1]
            s.photos.append({"file_id": biggest["file_id"], "idx": len(s.photos)})
            await _send(deps, chat_id,
                f"📷 Фото *{len(s.photos)}* принято. Пришли следующее или жми «✅ Готово, к названиям».",
                kb=_kb_photos())
            return
        if text in ("✅ Готово, к названиям", "✅ Готово", "/next"):
            if not s.photos:
                await _send(deps, chat_id, "Сначала пришли хотя бы одно фото.", kb=_kb_photos())
                return
            if len(s.photos) > 10:
                await _send(deps, chat_id, f"Максимум 10 товаров. У тебя {len(s.photos)}. Жми /reset.",
                            kb=_kb_photos())
                return
            s.phase = "names"
            await _send(deps, chat_id,
                f"✅ Принято *{len(s.photos)}* фото.\n\n"
                f"Теперь название и артикул для *фото №1*.\nФормат: `Название, артикул`",
                kb=_kb_names())
            return
        if text:
            await _send(deps, chat_id, "Сейчас фаза приёма фото. Пришли фото или жми «✅ Готово».",
                        kb=_kb_photos())
        return

    if s.phase == "names":
        if photos:
            await _send(deps, chat_id, "Сейчас фаза приёма названий. Фото добавлять нельзя.",
                        kb=_kb_names())
            return
        if not text:
            await _send(deps, chat_id, "Жду название и артикул через запятую.", kb=_kb_names())
            return
        parts = [x.strip() for x in text.replace(";", ",").replace("\t", ",").split(",") if x.strip()]
        if len(parts) < 2:
            await _send(deps, chat_id,
                "Неверный формат. Должно быть: `Название, артикул`.\nПример: `Кофе Арабика 250г, COF-001`",
                kb=_kb_names())
            return
        name = ", ".join(parts[:-1])
        sku = parts[-1]
        s.products.append({"name": name, "sku": sku})
        next_idx = len(s.products)
        if next_idx < len(s.photos):
            await _send(deps, chat_id,
                f"✔️ Фото {next_idx}: {name} `{sku}`\n\n"
                f"Теперь название+артикул для *фото №{next_idx + 1}*:",
                kb=_kb_names())
            return
        # все названия введены → confirm
        s.phase = "confirm"
        cab = _cabinet_label(s.cabinet)
        dry = "вкл" if settings.DRY_RUN else "выкл"
        lines = ["📝 *Партия готова к запуску*", "",
                 f"🏪 Кабинет: *{cab}*",
                 f"⚙️ DRY_RUN: *{dry}*", ""]
        for i, p in enumerate(s.products, 1):
            lines.append(f"{i}) {p['name']} `{p['sku']}`")
        lines.append("")
        lines.append(f"Всего *{len(s.products)} товаров* × 4 фото = *{len(s.products) * 4}* изображений")
        # confirm-сообщение тоже inline
        await _send(deps, chat_id, "_Подтверди запуск_", kb=_kb_remove())
        res = await deps.tg.send_with_buttons(
            chat_id, "\n".join(lines),
            [
                [{"text": "▶ Запустить генерацию", "callback_data": "flow:run"}],
                [{"text": "✖ Отмена", "callback_data": "flow:cancel"}],
            ],
        )
        if res and res.get("message_id"):
            s.menu_message_id = res["message_id"]
        return

    if s.phase == "confirm":
        # Жмут на reply-кнопки в этой фазе — подсказываем
        await _send(deps, chat_id, "Жми кнопку под предыдущим сообщением: ▶ Запустить или ✖ Отмена.")
        return

    if s.phase == "running":
        await _send(deps, chat_id,
            "⏳ Партия уже идёт. Жди отчёт. /reset — отменит сессию (но не остановит уже запущенный пайплайн).")
        return


# ─── запуск pipeline ──────────────────────────────────────────────


async def _start_pipeline(chat_id: int, deps, s: TgSession) -> None:
    s.phase = "running"
    batch_id = f"{int(time.time())}-{uuid.uuid4().hex[:6]}"
    products = [
        ProductIn(idx=i, sku=p["sku"], name=p["name"], tg_file_id=s.photos[i]["file_id"])
        for i, p in enumerate(s.products)
    ]
    cabinet_names = (
        [c.name for c in settings.list_cabinets()] if s.cabinet == "all" else [s.cabinet]
    )
    req = RunRequest(batch_id=batch_id, chat_id=chat_id, products=products,
                     cabinet_names=cabinet_names)
    cab_label = _cabinet_label(s.cabinet)
    dry = "вкл" if settings.DRY_RUN else "выкл"

    if s.menu_message_id:
        await deps.tg.edit_message_text(
            chat_id, s.menu_message_id,
            f"🚀 *Партия* `{batch_id}` запущена\n\n"
            f"🏪 {cab_label}\n⚙️ DRY_RUN: {dry}\n\n_Прогресс пришлю отдельными сообщениями._",
            buttons=[],
        )
    else:
        await _send(deps, chat_id,
                    f"🚀 Партия `{batch_id}` запущена → {cab_label} (DRY_RUN: {dry})",
                    kb=_kb_remove())

    asyncio.create_task(_run_and_cleanup(req, deps, chat_id))


async def _run_and_cleanup(req: RunRequest, deps, chat_id: int) -> None:
    from .pipeline import run_batch
    try:
        await run_batch(req, deps)
    finally:
        s = _sessions.get(chat_id)
        if s and s.phase == "running":
            cab = s.cabinet
            _sessions[chat_id] = TgSession(cabinet=cab)
            # после завершения покажем главное меню
            await _send_main_menu(deps, chat_id, _sessions[chat_id])
