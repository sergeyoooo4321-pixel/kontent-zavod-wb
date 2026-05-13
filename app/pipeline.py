from __future__ import annotations

import asyncio
from datetime import datetime

from app.config import Settings
from app.excel_export import build_zip
from app.image_ai import ImageGenerator
from app.models import GeneratedImage, PhotoIn, ProductInput, ProductResult
from app.storage import Storage, content_hash
from app.telegram import TelegramClient


class BatchProcessor:
    def __init__(self, settings: Settings, telegram: TelegramClient, storage: Storage, image_generator: ImageGenerator):
        self.settings = settings
        self.telegram = telegram
        self.storage = storage
        self.image_generator = image_generator
        self._sem = asyncio.Semaphore(settings.MAX_PARALLEL_PRODUCTS)

    async def process(self, chat_id: int, batch_id: str, photos: list[PhotoIn], products: list[ProductInput]) -> None:
        await self.telegram.send_message(chat_id, f"🟦 Запускаю партию `{batch_id}`: товаров {len(products)}.")
        pairs = list(zip(photos, products, strict=True))
        tasks = [self._process_one(batch_id, photo, product) for photo, product in pairs]
        results = await asyncio.gather(*tasks)
        zip_bytes = build_zip(results, self.settings)
        await self.telegram.send_document(
            chat_id,
            zip_bytes,
            filename=f"content-zavod-{batch_id}.zip",
            caption=f"Готово: {len(results)} товаров, 4 фото на товар, Excel Ozon/WB внутри.",
        )
        await self.telegram.send_message(chat_id, "✅ Партия собрана. Можно начать новую через /start.")

    async def _process_one(self, batch_id: str, photo: PhotoIn, product: ProductInput) -> ProductResult:
        async with self._sem:
            source_bytes, file_path = await self.telegram.get_file_bytes(photo.file_id)
            base_key = f"{batch_id}/{product.sku}-{content_hash(source_bytes)}"
            source_url, source_key = self.storage.put_public(f"{base_key}/source.jpg", source_bytes, "image/jpeg")
            images = [
                GeneratedImage(role="source", url=source_url, key=source_key, bytes_data=source_bytes),
            ]
            warnings: list[str] = []
            generated = await self.image_generator.generate_set(product, source_bytes)
            for item in generated:
                url, key = self.storage.put_public(f"{base_key}/{item.role}.jpg", item.content, "image/jpeg")
                images.append(GeneratedImage(role=item.role, url=url, key=key, bytes_data=item.content))
                if item.warning:
                    warnings.append(f"{product.sku}: {item.warning}")
            if file_path:
                warnings.append(f"{product.sku}: source telegram path {file_path}")
            return ProductResult(input=product, images=images, warnings=warnings)


def new_batch_id(chat_id: int) -> str:
    return f"{chat_id}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"

