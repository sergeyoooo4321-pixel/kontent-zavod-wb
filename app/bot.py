from __future__ import annotations

import asyncio
from typing import Any

from app.db import StateStore
from app.models import BotState, PhotoIn
from app.parsing import parse_product_text
from app.pipeline import BatchProcessor, new_batch_id
from app.telegram import TelegramClient, keyboard


DONE_WORDS = {"готово", "готов", "done", "/done"}


class Bot:
    def __init__(self, store: StateStore, telegram: TelegramClient, processor: BatchProcessor, max_photos: int):
        self.store = store
        self.telegram = telegram
        self.processor = processor
        self.max_photos = max_photos

    async def handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message") or update.get("edited_message")
        if not message:
            return
        chat = message.get("chat") or {}
        chat_id = int(chat.get("id"))
        text = (message.get("text") or "").strip()

        if text in {"/start", "🚀 Новая партия"}:
            await self._start(chat_id)
            return
        if text in {"/reset", "🔄 Сбросить"}:
            self.store.delete(chat_id)
            await self.telegram.send_message(chat_id, "Сессия сброшена. Нажми /start, чтобы начать заново.")
            return

        state = self.store.get(chat_id)
        if state is None:
            state = BotState(batch_id=new_batch_id(chat_id))
            self.store.set(chat_id, state)

        photo = self._extract_photo(message)
        if photo:
            await self._handle_photo(chat_id, state, photo)
            return

        if text.lower() in DONE_WORDS or text == "✅ Готово":
            await self._finish_photos(chat_id, state)
            return

        if text:
            await self._handle_text(chat_id, state, text)
            return

        await self.telegram.send_message(chat_id, "Пришли фото товара файлом/фото или команду /start.")

    async def _start(self, chat_id: int) -> None:
        state = BotState(batch_id=new_batch_id(chat_id))
        self.store.set(chat_id, state)
        await self.telegram.send_message(
            chat_id,
            "Загружай фото товаров по одному. Я буду нумеровать: Фото 1, Фото 2... Когда закончишь, нажми `✅ Готово`.",
            keyboard(["✅ Готово"], ["🔄 Сбросить"]),
        )

    async def _handle_photo(self, chat_id: int, state: BotState, photo: PhotoIn) -> None:
        if state.phase != "collecting_photos":
            await self.telegram.send_message(chat_id, "Сейчас я жду текстовые данные по фото. Если нужно начать заново — /reset.")
            return
        if len(state.photos) >= self.max_photos:
            await self.telegram.send_message(chat_id, f"Достигнут лимит {self.max_photos} фото на партию. Нажми `Готово`.")
            return
        photo.index = len(state.photos) + 1
        state.photos.append(photo)
        self.store.set(chat_id, state)
        await self.telegram.send_message(chat_id, f"Фото {photo.index} принято. Загружай следующее или нажми `✅ Готово`.")

    async def _finish_photos(self, chat_id: int, state: BotState) -> None:
        if state.phase != "collecting_photos":
            await self.telegram.send_message(chat_id, "Фото уже закрыты. Сейчас введи данные по текущему фото или /reset.")
            return
        if not state.photos:
            await self.telegram.send_message(chat_id, "Сначала загрузи хотя бы одно фото.")
            return
        state.phase = "collecting_items"
        state.current_item_index = 1
        self.store.set(chat_id, state)
        await self._ask_item(chat_id, state)

    async def _ask_item(self, chat_id: int, state: BotState) -> None:
        idx = state.current_item_index
        await self.telegram.send_message(
            chat_id,
            (
                f"Фото {idx}/{len(state.photos)}. Введи данные одним сообщением.\n\n"
                "Формат лучше такой:\n"
                "артикул: 59031\n"
                "бренд: Tide\n"
                "название: Стиральный порошок Альпийская свежесть 400 г\n"
                "доп: вес 400 г, габариты 10x6x20, цена 0"
            ),
            keyboard(["🔄 Сбросить"]),
        )

    async def _handle_text(self, chat_id: int, state: BotState, text: str) -> None:
        if state.phase == "collecting_photos":
            await self.telegram.send_message(chat_id, "Сейчас этап фото. Загружай фото по одному или нажми `✅ Готово`.")
            return
        if state.phase == "processing":
            await self.telegram.send_message(chat_id, "Партия уже обрабатывается. Дождись ZIP или сбрось /reset.")
            return
        if state.phase != "collecting_items":
            await self.telegram.send_message(chat_id, "Нажми /start, чтобы начать новую партию.")
            return
        try:
            product = parse_product_text(state.current_item_index, text)
        except Exception as exc:  # noqa: BLE001
            await self.telegram.send_message(chat_id, f"Не смог разобрать данные: {exc}. Повтори для Фото {state.current_item_index}.")
            return
        state.products.append(product)
        if len(state.products) < len(state.photos):
            state.current_item_index += 1
            self.store.set(chat_id, state)
            await self._ask_item(chat_id, state)
            return

        state.phase = "processing"
        self.store.set(chat_id, state)
        await self.telegram.send_message(chat_id, "Данные приняты. Генерирую фото, ссылки и Excel. Это может занять несколько минут.")
        asyncio.create_task(self.processor.process(chat_id, state.batch_id, state.photos, state.products))

    def _extract_photo(self, message: dict[str, Any]) -> PhotoIn | None:
        photos = message.get("photo") or []
        if photos:
            best = photos[-1]
            return PhotoIn(index=1, file_id=best["file_id"], kind="photo")
        document = message.get("document")
        if document:
            mime = document.get("mime_type") or ""
            name = document.get("file_name") or ""
            if mime.startswith("image/") or name.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                return PhotoIn(index=1, file_id=document["file_id"], kind="document", file_name=name, mime_type=mime)
        return None

