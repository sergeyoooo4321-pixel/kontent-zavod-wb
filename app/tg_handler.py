"""Telegram update handler с reply-клавиатурой и multi-cabinet UX.

Вся навигация — через reply-keyboard (кнопки внизу экрана, всегда видны).
В каждом подменю есть «◀️ Назад» — возврат в главное меню (или на предыдущий шаг).

Состояния (TgSession.phase):
  idle              — главное меню
  cabinet_select    — выбор кабинета (Профит / Прогресс 24 / 247 / ТНП)
  settings          — настройки (выбор кабинета, очистить разговор)
  gnome_chat        — чат с гномом (главный сценарий, ZIP-фабрика)
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .config import settings

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


# ─── метки для UI ────────────────────────────────────────────────


# Канон названий кнопок
BTN_NEW_BATCH = "📦 Новая партия"  # legacy — на нажатие переводим в gnome_chat
BTN_GNOME = "🧙 Чат с гномом"
BTN_CABINET_PREFIX = "🏪 Кабинет:"  # динамический суффикс
BTN_SETTINGS = "⚙️ Настройки"
BTN_HELP = "ℹ️ Помощь"
BTN_BACK = "◀️ Назад в меню"
BTN_GNOME_RESET = "🧙 Очистить разговор"

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
        [BTN_GNOME],
        [BTN_SETTINGS],
    ])


def _kb_gnome() -> dict:
    return _kb([
        [BTN_BACK],
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
    rows.append([BTN_BACK])
    return _kb(rows)


def _kb_settings(s: TgSession) -> dict:
    return _kb([
        [_btn_cabinet_top(s)],
        [BTN_GNOME_RESET],
        [BTN_HELP],
        [BTN_BACK],
    ])


# ─── helpers ──────────────────────────────────────────────────────


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
        f"🏪 Кабинет: *{cab}*\n\n"
        "Жми *🧙 Чат с гномом* и кидай фото + название — гном сделает "
        "4 фото на товар, подберёт категории, заполнит xlsx-шаблоны "
        "Ozon и WB и пришлёт ZIP. Дальше сам загрузишь в кабинеты МП.\n\n"
        "_Любой текст в этом меню тоже уходит гному._"
    )


async def _show_main_menu(deps, chat_id: int, s: TgSession) -> None:
    await _send(deps, chat_id, _main_menu_text(s), kb=_kb_main(s))


# ─── мост к гному (cz-gnome.service на :8001) ─────────────────────


_GNOME_URL = "http://127.0.0.1:8001"


async def _ask_gnome(
    deps,
    chat_id: int,
    text: str,
    images: list[str] | None = None,
) -> tuple[str, bool]:
    """Шлёт сообщение в cz-gnome.service и возвращает (reply, approval_required).

    Гном живёт в ../gnome/ как отдельный сервис на :8001. Сессии у него
    per chat_id — нить разговора сохраняется автоматически.
    Если в reply есть approval-маркер — возвращаем флаг True, чтобы bridge
    нарисовал inline-кнопки [✅ Одобряю] [❌ Перегенерить].
    """
    try:
        r = await deps.http.post(
            f"{_GNOME_URL}/chat",
            json={"chat_id": chat_id, "text": text, "images": images or []},
            timeout=300.0,
        )
        if r.status_code >= 400:
            logger.warning("gnome %s: %s", r.status_code, r.text[:200])
            return f"🧙 Гном задумался: HTTP {r.status_code}", False
        data = r.json()
        reply = (data.get("reply") or "").strip() or "🧙 Гном промолчал."
        return reply, bool(data.get("approval_required"))
    except Exception as e:
        logger.warning("gnome bridge fail chat=%s: %s", chat_id, e)
        return f"🧙 Гном недоступен: {str(e)[:120]}", False


async def _gnome_reset(deps, chat_id: int) -> bool:
    try:
        r = await deps.http.post(f"{_GNOME_URL}/sessions/{chat_id}/reset", timeout=10.0)
        return r.status_code < 400
    except Exception as e:
        logger.warning("gnome reset fail: %s", e)
        return False


async def _send_gnome_reply(deps, chat_id: int, reply: str, approval: bool, kb: dict) -> None:
    """Отправить ответ гнома: либо обычный текст, либо с approval-кнопками."""
    if approval:
        await deps.tg.send_with_buttons(
            chat_id,
            reply,
            [[
                {"text": "✅ Одобряю", "callback_data": "gnome:approve:yes"},
                {"text": "❌ Перегенерить", "callback_data": "gnome:approve:no"},
            ]],
            parse_mode=None,
        )
    else:
        await _send(deps, chat_id, reply, kb=kb, parse_mode=None)


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
        f"🏪 Текущий кабинет: *{cab}*\n\n"
        "Кабинет влияет на: какие токены WB API использовать при подгрузке "
        "справочников, и в какую папку складывать кеш xlsx-шаблонов."
    )
    await _send(deps, chat_id, text, kb=_kb_settings(s))


def _help_text() -> str:
    return (
        "*Как пользоваться:*\n\n"
        "1️⃣  Жми *🧙 Чат с гномом*.\n"
        "2️⃣  Кидай фотку первого товара одним сообщением.\n"
        "3️⃣  Следующим сообщением пиши `артикул, бренд - название` "
        "(пример: `59031, Tide - Стиральный порошок Альпийская свежесть 400 г`).\n"
        "4️⃣  Повторяй для каждого товара (до 10 на партию).\n"
        "5️⃣  Когда всё собрал — пиши «поехали».\n"
        "6️⃣  Гном подтвердит партию, спросит «точно?» — ответь «да».\n"
        "7️⃣  Через 2-3 минуты получишь *ZIP-архив* в чат: фото + xlsx-шаблоны "
        "Ozon и WB + инструкция. Скачивай, распаковывай, грузи в свои "
        "кабинеты МП через «Загрузить через xls-шаблон».\n\n"
        "*Один раз на категорию:* если у гнома нет в кеше пустого xlsx для "
        "новой категории — он попросит скинуть. Скачиваешь в кабинете МП "
        "пустой шаблон, кидаешь файлом в чат — гном запоминает навсегда.\n\n"
        "*⚙️ Настройки:* выбор кабинета (для папки в кеше шаблонов и токенов "
        "WB API), очистить разговор с гномом, эта помощь.\n\n"
        "Команды: `/start` `/help` `/reset`"
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
        cq_data = cq.get("data") or ""
        logger.info("tg.update callback_query chat=%s data=%s", chat_id, cq_data)
        # gnome:approve:yes / gnome:approve:no
        if cq_data.startswith("gnome:approve:") and chat_id:
            try:
                await deps.tg.answer_callback_query(cq.get("id") or "")
            except Exception:
                pass
            answer = cq_data.split(":", 2)[-1]
            user_text = "✅ Одобряю, продолжай" if answer == "yes" else "❌ Не нравится, перегенери"
            try:
                await deps.tg.send_chat_action(chat_id, "typing")
            except Exception:
                pass
            reply, approval = await _ask_gnome(deps, chat_id, user_text)
            s = _get_session(chat_id)
            kb = _kb_gnome() if s.phase == "gnome_chat" else _kb_main(s)
            await _send_gnome_reply(deps, chat_id, reply, approval, kb)
            return
        # pipeline:phase1:yes / pipeline:phase1:no — approval после Этапа 1
        if cq_data.startswith("pipeline:phase1:") and chat_id:
            try:
                await deps.tg.answer_callback_query(cq.get("id") or "")
            except Exception:
                pass
            accepted = cq_data.endswith(":yes")
            from . import pipeline as _pipe
            # Запускаем resume в фоне — callback_query handler не должен висеть.
            asyncio.create_task(
                _pipe.resume_after_phase1_approval(chat_id, accepted, deps)
            )
            return
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


def _extract_xlsx_doc(msg: dict) -> tuple[str, str] | None:
    """Возвращает (file_id, filename) для xlsx-документа из message.

    Используется в gnome_chat фазе для загрузки шаблонов Ozon/WB которые
    юзер шлёт как Document.
    """
    doc = msg.get("document") or {}
    fid = doc.get("file_id")
    if not fid:
        return None
    fname = (doc.get("file_name") or "").strip()
    mime = (doc.get("mime_type") or "").lower()
    is_xlsx = (
        fname.lower().endswith(".xlsx")
        or "spreadsheetml" in mime
        or "openxmlformats-officedocument" in mime
    )
    if not is_xlsx:
        return None
    if not fname:
        fname = f"template_{int(time.time())}.xlsx"
    return fid, fname


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
    if text == "/reset":
        # /reset теперь сбрасывает сессию гнома, а не partial-партию
        await _gnome_reset(deps, chat_id)
        s.phase = "idle"
        await _send(deps, chat_id,
            "🧹 История разговора с гномом очищена.", kb=_kb_main(s))
        return

    # ── кнопка «Назад в меню» работает на любой фазе ─────
    if text == BTN_BACK:
        s.phase = "idle"
        await _show_main_menu(deps, chat_id, s)
        return

    # ── маршруты по фазам ────────────────────────────────

    if s.phase == "idle":
        # Для legacy-сессий: если юзер нажал старую кнопку «📦 Новая партия»
        # из персистентной reply-клавиатуры — переводим его в чат с гномом.
        if text == BTN_NEW_BATCH:
            s.phase = "gnome_chat"
            await _send(deps, chat_id,
                "🧙 Партии теперь делаются через чат с гномом — он сам "
                "проведёт через все этапы и спросит одобрения. Кидай фото "
                "и пиши артикул+название.",
                kb=_kb_gnome())
            return
        if text == BTN_SETTINGS:
            s.phase = "settings"
            await _show_settings(deps, chat_id, s)
            return
        if text == BTN_GNOME:
            s.phase = "gnome_chat"
            await _send(deps, chat_id,
                "🧙 *Чат с гномом.*\n\n"
                "Кидай фото и пиши что это (бренд, артикул, название) — я сделаю "
                "варианты упаковки, спрошу одобрения и соберу карточку. "
                "Или просто разговаривай — я помню контекст и могу заглянуть в код "
                "и логи проекта если попросишь.",
                kb=_kb_gnome())
            return
        # любой произвольный текст в idle — переадресуем гному
        if text and not text.startswith("/"):
            try:
                await deps.tg.send_chat_action(chat_id, "typing")
            except Exception:
                pass
            reply, approval = await _ask_gnome(deps, chat_id, text)
            await _send_gnome_reply(deps, chat_id, reply, approval, _kb_main(s))
            return
        # фото или иной не-текст — показать меню
        await _show_main_menu(deps, chat_id, s)
        return

    if s.phase == "cabinet_select":
        # сравним по началу строки (точные label кабинетов в кнопках)
        for c in settings.list_cabinets():
            if text.startswith(c.label):
                s.cabinet = c.name
                s.phase = "settings"
                await _send(deps, chat_id,
                    f"✅ Кабинет: *{c.label}*",
                    kb=_kb_settings(s))
                return
        # не распознали — повторим
        await _show_cabinet_menu(deps, chat_id)
        return

    if s.phase == "settings":
        if text.startswith(BTN_CABINET_PREFIX):
            s.phase = "cabinet_select"
            await _show_cabinet_menu(deps, chat_id)
            return
        if text == BTN_HELP:
            await _send(deps, chat_id, _help_text(), kb=_kb_settings(s))
            return
        if text == BTN_GNOME_RESET:
            ok = await _gnome_reset(deps, chat_id)
            msg_txt = "🧙 История разговора с гномом очищена." if ok \
                      else "🧙 Не получилось сбросить разговор."
            await _send(deps, chat_id, msg_txt, kb=_kb_settings(s))
            return
        # не распознали — повторим
        await _show_settings(deps, chat_id, s)
        return

    # Legacy фазы photos/names/confirm/running (старый кнопочный flow)
    # удалены — если кто-то застрял в них, переводим в gnome_chat.
    if s.phase in ("photos", "names", "confirm", "running"):
        s.phase = "gnome_chat"
        await _send(deps, chat_id,
            "Старый кнопочный сценарий удалён. Теперь всё через гнома — "
            "кидай фото и пиши `артикул, название`, в конце скажи «поехали».",
            kb=_kb_gnome())
        return

    if s.phase == "gnome_chat":
        # xlsx-документ — сохраняем локально и сообщаем гному путь
        xlsx = _extract_xlsx_doc(msg)
        if xlsx is not None:
            file_id, filename = xlsx
            try:
                await deps.tg.send_chat_action(chat_id, "upload_document")
            except Exception:
                pass
            try:
                raw = await deps.tg.get_file_bytes(file_id)
            except Exception as e:
                await _send(deps, chat_id, f"Не смог скачать xlsx: {e}", kb=_kb_gnome())
                return
            from pathlib import Path
            uploads_dir = Path.home() / "cz-backend" / "uploads" / str(chat_id)
            uploads_dir.mkdir(parents=True, exist_ok=True)
            # Если файл с таким именем уже есть — добавляем суффикс времени.
            xlsx_path = uploads_dir / filename
            if xlsx_path.exists():
                stem = xlsx_path.stem
                suffix = xlsx_path.suffix
                xlsx_path = uploads_dir / f"{stem}_{int(time.time())}{suffix}"
            try:
                xlsx_path.write_bytes(raw)
            except Exception as e:
                await _send(deps, chat_id, f"Не смог сохранить xlsx: {e}", kb=_kb_gnome())
                return
            size_kb = len(raw) // 1024
            await _send(deps, chat_id,
                f"📋 xlsx сохранил ({size_kb} КБ). Передаю гному путь.",
                kb=_kb_gnome(), parse_mode=None)
            try:
                await deps.tg.send_chat_action(chat_id, "typing")
            except Exception:
                pass
            note = (
                f"Юзер прислал xlsx-шаблон. Я (бот) сохранил его на сервере "
                f"по абсолютному пути: {xlsx_path}. "
                f"Имя файла: {filename}. "
                "Можешь распарсить через скилл parse_template."
            )
            if text:
                note += f"\n\nКомментарий юзера: {text}"
            reply, approval = await _ask_gnome(deps, chat_id, note)
            await _send_gnome_reply(deps, chat_id, reply, approval, _kb_gnome())
            return

        # Фото в режиме гнома: качаем → S3 → URL → гному с vision
        if has_image:
            try:
                await deps.tg.send_chat_action(chat_id, "upload_photo")
            except Exception:
                pass
            try:
                raw = await deps.tg.get_file_bytes(image_file_id)
            except Exception as e:
                await _send(deps, chat_id, f"Не смог скачать фото из TG: {e}", kb=_kb_gnome())
                return
            try:
                from .s3 import S3Client
                key = f"gnome-{chat_id}/{int(time.time())}-{uuid.uuid4().hex[:6]}.jpg"
                src_url = await deps.s3.put_public(key, raw, "image/jpeg")
            except Exception as e:
                await _send(deps, chat_id, f"S3 не принял фото: {e}", kb=_kb_gnome())
                return

            caption = text or ""
            await _send(deps, chat_id,
                "📥 Принял фото. Передаю гному…",
                kb=_kb_gnome(), parse_mode=None)
            try:
                await deps.tg.send_chat_action(chat_id, "typing")
            except Exception:
                pass
            reply, approval = await _ask_gnome(deps, chat_id, caption, images=[src_url])
            await _send_gnome_reply(deps, chat_id, reply, approval, _kb_gnome())
            return

        # Чистый текст в режиме гнома
        if text:
            try:
                await deps.tg.send_chat_action(chat_id, "typing")
            except Exception:
                pass
            reply, approval = await _ask_gnome(deps, chat_id, text)
            await _send_gnome_reply(deps, chat_id, reply, approval, _kb_gnome())
            return

        # Что-то странное (например стикер) — показать инструкцию
        await _send(deps, chat_id,
            "🧙 В режиме гнома — кидай фото или пиши текстом.",
            kb=_kb_gnome())
        return


