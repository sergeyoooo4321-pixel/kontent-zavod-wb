"""ZIP-упаковщик финальной партии: фото + xlsx-шаблоны + README.

Юзер получает один документ в TG, разархивирует, грузит ozon/*.xlsx в
свой кабинет Ozon, wb/*.xlsx — в кабинет WB, photos/* загружаются
маркетплейсами по URL'ам которые уже подставлены в xlsx (так что
фото в ZIP — это только бэкап для юзера).
"""
from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)


_README_TEMPLATE = """\
Контент-завод — партия {batch_id}
================================

Что внутри:
- photos/       исходники + 4 сгенерированных фото на каждый товар
- ozon/         заполненные xlsx-шаблоны Ozon, по одному на категорию
- wb/           заполненные xlsx-шаблоны Wildberries, по одному на предмет

Куда грузить:
1) Ozon: личный кабинет → Товары и цены → Добавить → Через xls-шаблон →
   «Загрузить из файла». Категория в кабинете должна совпадать с той
   что в имени файла. Файлы внутри xlsx уже содержат публичные URL
   фотографий — Ozon скачает их сам, повторно загружать фото руками не
   надо. Если нужно — фото есть в папке photos/ как бэкап.

2) Wildberries: личный кабинет → Карточка товара → Добавить →
   Много товаров → «Загрузить из файла». В xlsx тоже стоят URL фото,
   WB скачает.

Если что-то отвергнуто маркетплейсом — открой файл в Excel, поправь
ячейку (например подправь значение справочника, добавь недостающее
поле) и загрузи ещё раз. Шаблоны живые — можно править в самом файле,
структуру не ломать.

Партия собрана: {n_products} товаров, {n_skus} SKU (с учётом x2/x3),
{n_ozon} ozon-шаблонов, {n_wb} wb-шаблонов, {n_photos} фотографий.
"""


async def build_batch_zip(
    *,
    batch_id: str,
    photo_urls: dict[str, dict[str, str]],
    xlsx_paths: dict[str, Path],
    http: httpx.AsyncClient,
    n_products: int,
    n_skus: int,
) -> bytes:
    """Собрать ZIP из фото-URL'ов + заполненных xlsx + README.

    photo_urls: {sku: {tag: public_url}}  — URL'ы S3 (главное / pack2 / pack3 / extra)
    xlsx_paths: {имя_в_zip.xlsx: Path_к_файлу}  — заполненные шаблоны
    """
    buf = io.BytesIO()
    n_photos = 0
    n_ozon = sum(1 for k in xlsx_paths if k.startswith("ozon/"))
    n_wb = sum(1 for k in xlsx_paths if k.startswith("wb/"))

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        # 1. Фото — качаем из S3 и пакуем
        for sku, tags in (photo_urls or {}).items():
            for tag, url in (tags or {}).items():
                if not url:
                    continue
                try:
                    r = await http.get(url, timeout=60.0, follow_redirects=True)
                    if r.status_code >= 400:
                        logger.warning("zip fetch %s/%s: HTTP %s", sku, tag, r.status_code)
                        continue
                    arc_name = f"photos/{sku}_{tag}.jpg"
                    z.writestr(arc_name, r.content)
                    n_photos += 1
                except Exception as e:
                    logger.warning("zip fetch %s/%s: %s", sku, tag, str(e)[:120])

        # 2. xlsx-шаблоны
        for arc_name, p in (xlsx_paths or {}).items():
            try:
                data = Path(p).read_bytes()
                z.writestr(arc_name, data)
            except Exception as e:
                logger.warning("zip xlsx %s: %s", arc_name, str(e)[:120])

        # 3. README
        readme = _README_TEMPLATE.format(
            batch_id=batch_id,
            n_products=n_products,
            n_skus=n_skus,
            n_ozon=n_ozon,
            n_wb=n_wb,
            n_photos=n_photos,
        )
        z.writestr("README.txt", readme.encode("utf-8"))

    logger.info("zip built batch=%s size=%dB photos=%d ozon=%d wb=%d",
                batch_id, buf.tell(), n_photos, n_ozon, n_wb)
    return buf.getvalue()
