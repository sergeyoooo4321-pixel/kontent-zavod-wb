"""Telegram update handler с reply-клавиатурой и multi-cabinet UX.

Вся навигация — через reply-keyboard (кнопки внизу экрана, всегда видны).
В каждом подменю есть «◀️ Назад» — возврат в главное меню (или на предыдущий шаг).

Состояния (TgSession.phase):
  idle              — главное меню
  cabinet_select    — выбор кабинета (Профит / Прогресс 24 / 247 / ТНП / mirror)
  settings          — настройки (DRY_RUN toggle, текущий кабинет)
  photos            — приём фото
  names             — приём названий+артикулов
  confirm           — подтверждение запуска
  running           — пайплайн идёт
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .config import settings
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


_sessions: dict[int, TgSession] = {}


def _get_session(chat_id: int) -> TgSession:
    s = _sessions.get(chat_id)
    if s is None or (time.time() - s.started_at) > 86400:
        s = TgSession()
        _sessions[chat_id] = s
    return s


def _reset_partial(chat_id: int) -> TgSession:
    """Сбрасывает партию (фото/названия), сохраняя cabinet."""
    cab = _sessions.get(chat_id).cabinet if _sessions.get(chat_id) else None
    s = TgSession(cabinet=cab)
    _sessions[chat_id] = s
    return s


# ─── метки для UI ────────────────────────────────────────────────


# Канон названий кнопок — собраны в одном месте чтобы не разъезжалось
BTN_NEW_BATCH = "📦 Новая партия"
BTN_CABINET_PREFIX = "🏪 Кабинет:"  # динамический суффикс
BTN_SETTINGS = "⚙️ Настройки"
BTN_HELP = "ℹ️ Помощь"
BTN_BACK = "◀️ Назад в меню"
BTN_PHOTOS_DONE = "✅ Готово, к названиям"
BTN_RESET = "🔄 Сбросить партию"
BTN_RUN = "▶️ Запустить генерацию"
BTN_CONFIRM_BACK = "◀️ Назад к названиям"
BTN_CABINET_ALL = "🔄 Все сразу (mirror)"

CABINET_DETAILS = {
    "profit": "Профит",
    "progress24": "Прогресс 24",
    "progress247": "Прогресс 247",
    "tnp": "ТНП",
    "default": "Default",
}


def _cabinet_label(name: str | None) -> str:
    if name == "all":
        return "🔄 Все сразу"
    if not name:
        return "не выбран"
    return CABINET_DETAILS.get(name, name)


def _cabinet_button_label(c) -> str:
    """Кнопка для одного кабинета: «Профит ✓О+В» / «Прогресс 247 ✓В»."""
    marks = []
    if c.has_ozon:
        marks.append("О")
    if c.has_wb:
        marks.append("В")
    suffix = f" ✓{'+'.join(marks)}" if marks else " (нет токенов)"
    return f"{c.label}{suffix}"


def _btn_cabinet_top(s: TgSession) -> str:
    return f"{BTN_CABINET_PREFIX} {_cabinet_label(s.cabinet)}"


# ─── reply-клавиатуры по фазам ────────────────────────────────────


def _kb(rows: list[list[str]]) -> dict:
    return {
        "keyboard": [[{"text": t} for t in row] for row in rows],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def _kb_main(s: TgSession) -> dict:
    return _kb([
        [BTN_NEW_BATCH],
        [_btn_cabinet_top(s)],
        [BTN_SETTINGS, BTN_HELP],
    ])


def _kb_cabinets() -> dict:
    rows: list[list[str]] = []
    cabs = settings.list_cabinets()
    pair: list[str] = []
    for c in cabs:
        pair.append(_cabinet_button_label(c))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    if cabs:
        rows.append([BTN_CABINET_ALL])
    rows.append([BTN_BACK])
    return _kb(rows)


def _kb_settings() -> dict:
    dry_btn = ("⚙️ DRY_RUN: ✅ вкл (нажми чтобы выключить)"
               if settings.DRY_RUN
               else "⚙️ DRY_RUN: ❌ выкл (нажми чтобы ВКЛ-чить)")
    return _kb([
        [dry_btn],
        [BTN_BACK],
    ])


def _kb_photos() -> dict:
    return _kb([
        [BTN_PHOTOS_DONE],
        [BTN_RESET],
        [BTN_BACK],
    ])


def _kb_names() -> dict:
    return _kb([
        [BTN_RESET],
        [BTN_BACK],
    ])


def _kb_confirm() -> dict:
    return _kb([
        [BTN_RUN],
        [BTN_CONFIRM_BACK],
        [BTN_RESET],
    ])


def _kb_running() -> dict:
    return _kb([
        [BTN_BACK],
    ])


# ─── helpers ──────────────────────────────────────────────────────


def _settings_dry_btn_match(text: str) -> bool:
    """Любой вариант DRY_RUN-кнопки (вкл/выкл) — это toggle."""
    return text.startswith("⚙️ DRY_RUN:")


def _dry_text() -> str:
    return "✅ вкл" if settings.DRY_RUN else "❌ выкл"


async def _send(deps, chat_id: int, text: str, kb: dict | None = None,
                parse_mode: str | None = "Markdown") -> None:
    """Send text + optional reply-keyboard. Markdown с фолбэком в plain."""
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
    except Exception as e:
        logger.warning("tg send fail: %s", e)


# ─── экраны ───────────────────────────────────────────────────────


def _main_menu_text(s: TgSession) -> str:
    cab = _cabinet_label(s.cabinet)
    return (
        "🏭 *Контент-завод*\n\n"
        f"🏪 Кабинет: *{cab}*\n"
        f"⚙️ DRY\\_RUN: *{_dry_text()}*\n\n"
        "_DRY\\_RUN — заглушка. Когда вкл — карточки на МП НЕ публикуются, "
        "а в чат приходит JSON с тем что бы ушло._"
    )


async def _show_main_menu(deps, chat_id: int, s: TgSession) -> None:
    await _send(deps, chat_id, _main_menu_text(s), kb=_kb_main(s))


async def _show_cabinet_menu(deps, chat_id: int) -> None:
    cabs = settings.list_cabinets()
    if not cabs:
        await _send(deps, chat_id,
            "⚠️ *Кабинеты не настроены.*\n\nДобавь токены в `.env`: "
            "`OZON_PROFIT_CLIENT_ID/OZON_PROFIT_API_KEY`, `WB_PROFIT_TOKEN`, и т.п.",
            kb=_kb_main(_get_session(chat_id)))
        return
    lines = [
        "🏪 *Выбери кабинет.*",
        "",
        "✓О = Ozon настроен, ✓В = WB настроен",
        "*🔄 Все сразу* — карточка во всех кабинетах одним прогоном (mirror)",
    ]
    await _send(deps, chat_id, "\n".join(lines), kb=_kb_cabinets())


async def _show_settings(deps, chat_id: int, s: TgSession) -> None:
    cab = _cabinet_label(s.cabinet)
    text = (
        "⚙️ *Настройки*\n\n"
        f"🏪 Текущий кабинет: *{cab}*\n"
        f"⚙️ DRY\\_RUN: *{_dry_text()}*\n\n"
        "_При DRY\\_RUN=вкл этап заливки на МП собирает payload и шлёт сюда "
        "JSON-документом — карточки в кабинетах НЕ создаются. Когда выключишь — "
        "карточки реально создадутся через API._"
    )
    await _send(deps, chat_id, text, kb=_kb_settings())


def _help_text() -> str:
    return (
        "*Как пользоваться:*\n\n"
        "1️⃣  Жми *📦 Новая партия*.\n"
        "2️⃣  Если кабинет не выбран — выбираешь на следующем экране.\n"
        "    *Профит / Прогресс 24 / Прогресс 247 / ТНП* — конкретный кабинет.\n"
        "    *🔄 Все сразу* — карточка появится во всех кабинетах одним прогоном.\n"
        "3️⃣  Кидаешь фото товаров — *по одному сообщению*. Можно как обычное фото "
        "(TG сжимает) или как файл-картинку («Прикрепить → Файл», без сжатия). Жми *✅ Готово*.\n"
        "4️⃣  Для каждого фото пишешь `Артикул, Название`.\n"
        "5️⃣  Подтверждаешь — я генерирую 4 фото на товар + создаю карточки на МП.\n\n"
        "*⚙️ Настройки:*\n"
        "• *DRY\\_RUN* — переключатель заглушки. Когда *вкл* — этап заливки "
        "на маркетплейсы НЕ выполняется, payload приходит в чат как JSON. "
        "Безопасно тестить без публикации товаров.\n\n"
        "*◀️ Назад в меню* — возвращает в главное меню в любой момент.\n"
        "*🔄 Сбросить партию* — чистит все фото и названия (кабинет сохраняется).\n\n"
        "Команды: `/start` `/help`"
    )


# ─── главный обработчик update ────────────────────────────────────


async def handle_update(update: dict, deps) -> None:
    """Вся навигация через reply-кнопки → текстовые сообщения.

    Если приходит callback_query (старое inline-меню до перехода на reply-keyboard),
    закрываем «крутилку» и шлём свежее главное меню.
    """
    cq = update.get("callback_query")
    if cq:
        chat_id = (cq.get("message") or {}).get("chat", {}).get("id")
        logger.info("tg.update callback_query chat=%s data=%s", chat_id, cq.get("data"))
        await _handle_legacy_callback(cq, deps)
        return
    msg = update.get("message")
    if not msg:
        logger.info("tg.update kind=%s ignored",
                    next((k for k in update.keys() if k != "update_id"), "unknown"))
        return
    chat_id = (msg.get("chat") or {}).get("id")
    img_kind = (
        "photo" if msg.get("photo") else
        "doc-image" if (msg.get("document") or {}).get("mime_type", "").lower().startswith("image/") else
        "no"
    )
    text_preview = (msg.get("text") or "")[:40]
    logger.info("tg.update msg chat=%s image=%s text=%r", chat_id, img_kind, text_preview)
    await _handle_message(msg, deps)


async def _handle_legacy_callback(cq: dict, deps) -> None:
    """Совместимость со старыми inline-кнопками: отвечаем callback и шлём reply-меню."""
    cq_id = cq.get("id") or ""
    chat_id = (cq.get("message") or {}).get("chat", {}).get("id")
    try:
        await deps.tg.answer_callback_query(cq_id)
    except Exception:
        pass
    if not chat_id:
        return
    s = _get_session(chat_id)
    s.phase = "idle"
    await _send(deps, chat_id,
        "Меню обновилось — теперь кнопки внизу экрана. ⬇️",
        kb=_kb_main(s))


def _extract_image_file_id(msg: dict) -> str | None:
    """Возвращает file_id картинки из message.

    Поддерживает два варианта отправки:
      • message.photo — обычное фото (TG сжимает) → берём самый большой size
      • message.document — файл (без сжатия) с mime_type='image/*' → берём как есть
    """
    photos = msg.get("photo") or []
    if photos:
        return photos[-1].get("file_id")
    doc = msg.get("document") or {}
    mime = (doc.get("mime_type") or "").lower()
    if mime.startswith("image/"):
        return doc.get("file_id")
    return None


async def _handle_message(msg: dict, deps) -> None:
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    if not chat_id:
        return

    text = (msg.get("text") or "").strip()
    image_file_id = _extract_image_file_id(msg)
    has_image = image_file_id is not None
    s = _get_session(chat_id)

    # ── глобальные команды ───────────────────────────────
    if text in ("/start", "/menu"):
        s.phase = "idle"
        await _show_main_menu(deps, chat_id, s)
        return
    if text in ("/help", BTN_HELP):
        await _send(deps, chat_id, _help_text(), kb=_kb_main(s))
        return
    if text in ("/reset", BTN_RESET):
        s = _reset_partial(chat_id)
        await _send(deps, chat_id, "🧹 Партия сброшена. Кабинет сохранён.", kb=_kb_main(s))
        return

    # ── кнопка «Назад в меню» работает на любой фазе ─────
    if text == BTN_BACK:
        s.phase = "idle"
        await _show_main_menu(deps, chat_id, s)
        return

    # ── маршруты по фазам ────────────────────────────────

    if s.phase == "idle":
        if text == BTN_NEW_BATCH:
            if not s.cabinet:
                s.phase = "cabinet_select"
                await _show_cabinet_menu(deps, chat_id)
                return
            s.phase = "photos"
            s.photos = []
            s.products = []
            s.started_at = time.time()
            cab = _cabinet_label(s.cabinet)
            await _send(deps, chat_id,
                f"📦 *Новая партия* → кабинет *{cab}*\n\n"
                "Кидай фото товаров *по одному сообщению*.\n"
                "Можно как обычное фото (TG сжимает) или как файл-картинку "
                "(`Прикрепить → Файл`) — без сжатия, лучше для качества.\n\n"
                "Когда все — жми «✅ Готово, к названиям».",
                kb=_kb_photos())
            return
        if text.startswith(BTN_CABINET_PREFIX):
            s.phase = "cabinet_select"
            await _show_cabinet_menu(deps, chat_id)
            return
        if text == BTN_SETTINGS:
            s.phase = "settings"
            await _show_settings(deps, chat_id, s)
            return
        # любое другое — показать меню
        await _show_main_menu(deps, chat_id, s)
        return

    if s.phase == "cabinet_select":
        if text == BTN_CABINET_ALL:
            s.cabinet = "all"
            s.phase = "idle"
            await _send(deps, chat_id,
                "🔄 Выбран *mirror-режим* — следующая партия зальётся "
                "во ВСЕ настроенные кабинеты одним прогоном.",
                kb=_kb_main(s))
            return
        # сравним по началу строки (точные label кабинетов в кнопках)
        for c in settings.list_cabinets():
            if text.startswith(c.label):
                s.cabinet = c.name
                s.phase = "idle"
                await _send(deps, chat_id,
                    f"✅ Кабинет: *{c.label}*",
                    kb=_kb_main(s))
                return
        # не распознали — повторим
        await _show_cabinet_menu(deps, chat_id)
        return

    if s.phase == "settings":
        if _settings_dry_btn_match(text):
            settings.DRY_RUN = not settings.DRY_RUN
            logger.info("DRY_RUN toggled to %s by chat=%s", settings.DRY_RUN, chat_id)
            await _show_settings(deps, chat_id, s)
            return
        # не распознали — повторим
        await _show_settings(deps, chat_id, s)
        return

    if s.phase == "photos":
        if has_image:
            s.photos.append({"file_id": image_file_id, "idx": len(s.photos)})
            await _send(deps, chat_id,
                f"📷 Фото *{len(s.photos)}* принято. "
                "Пришли следующее или жми «✅ Готово».",
                kb=_kb_photos())
            return
        if text == BTN_PHOTOS_DONE:
            if not s.photos:
                await _send(deps, chat_id, "Сначала пришли хотя бы одно фото.", kb=_kb_photos())
                return
            if len(s.photos) > 10:
                await _send(deps, chat_id,
                    f"Максимум 10 товаров. У тебя {len(s.photos)}. Жми «🔄 Сбросить партию».",
                    kb=_kb_photos())
                return
            s.phase = "names"
            await _send(deps, chat_id,
                f"✅ Принято *{len(s.photos)}* фото.\n\n"
                f"Теперь артикул и название для *фото №1*.\nФормат: `Артикул, Название`",
                kb=_kb_names())
            return
        if text:
            await _send(deps, chat_id,
                "Сейчас фаза приёма фото. Пришли фотографию (как фото или как файл-картинку) "
                "или жми «✅ Готово».",
                kb=_kb_photos())
        return

    if s.phase == "names":
        if has_image:
            await _send(deps, chat_id,
                "Сейчас фаза приёма названий. Фото добавлять нельзя.",
                kb=_kb_names())
            return
        if not text:
            await _send(deps, chat_id, "Жду артикул и название через запятую.", kb=_kb_names())
            return
        parts = [x.strip() for x in text.replace(";", ",").replace("\t", ",").split(",") if x.strip()]
        if len(parts) < 2:
            await _send(deps, chat_id,
                "Неверный формат. Должно быть: `Артикул, Название`.\n"
                "Пример: `COF-001, Кофе Арабика 250г`",
                kb=_kb_names())
            return
        sku = parts[0]
        name = ", ".join(parts[1:])
        s.products.append({"name": name, "sku": sku})
        next_idx = len(s.products)
        if next_idx < len(s.photos):
            await _send(deps, chat_id,
                f"✔️ Фото {next_idx}: `{sku}` — {name}\n\n"
                f"Теперь артикул+название для *фото №{next_idx + 1}*:",
                kb=_kb_names())
            return
        # все названия введены → confirm
        s.phase = "confirm"
        cab = _cabinet_label(s.cabinet)
        lines = [
            "📝 *Партия готова к запуску*", "",
            f"🏪 Кабинет: *{cab}*",
            f"⚙️ DRY\\_RUN: *{_dry_text()}*", "",
        ]
        for i, p in enumerate(s.products, 1):
            lines.append(f"{i}) `{p['sku']}` — {p['name']}")
        lines.append("")
        lines.append(f"Всего *{len(s.products)} товаров* × 4 фото = "
                     f"*{len(s.products) * 4}* изображений")
        lines.append("")
        lines.append("Жми «▶️ Запустить генерацию».")
        await _send(deps, chat_id, "\n".join(lines), kb=_kb_confirm())
        return

    if s.phase == "confirm":
        if text == BTN_RUN:
            await _start_pipeline(chat_id, deps, s)
            return
        if text == BTN_CONFIRM_BACK:
            # вернёмся к редактированию названий — упрощаем: просим заново
            s.phase = "names"
            s.products = []
            await _send(deps, chat_id,
                "Возвращаемся к названиям. Введи снова `Артикул, Название` "
                f"для *фото №1* (всего {len(s.photos)}):",
                kb=_kb_names())
            return
        await _send(deps, chat_id,
            "Жми «▶️ Запустить генерацию», «◀️ Назад к названиям» или «🔄 Сбросить партию».",
            kb=_kb_confirm())
        return

    if s.phase == "running":
        await _send(deps, chat_id,
            "⏳ Партия уже идёт. Жди отчёт. /reset отменит сессию "
            "(но не остановит уже запущенный пайплайн).",
            kb=_kb_running())
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
    dry = _dry_text()

    await _send(deps, chat_id,
        f"🚀 *Партия* `{batch_id}` запущена\n\n"
        f"🏪 Кабинет: *{cab_label}*\n"
        f"⚙️ DRY\\_RUN: *{dry}*\n\n"
        "_Прогресс пришлю отдельными сообщениями._",
        kb=_kb_running())

    asyncio.create_task(_run_and_cleanup(req, deps, chat_id))


async def _run_and_cleanup(req: RunRequest, deps, chat_id: int) -> None:
    from .pipeline import run_batch
    try:
        await run_batch(req, deps)
    finally:
        s = _sessions.get(chat_id)
        if s and s.phase == "running":
            cab = s.cabinet
            new_s = TgSession(cabinet=cab)
            _sessions[chat_id] = new_s
            await _send(deps, chat_id,
                "🏁 *Партия завершена.* Можно запускать следующую.",
                kb=_kb_main(new_s))
