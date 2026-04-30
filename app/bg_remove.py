"""Background removal через rembg (U2Net локально).

Модель U2Net скачивается автоматически при первом вызове в ~/.u2net/ (~170MB).
rembg-сессия создаётся лениво — чтобы импорт модуля был дешёвым (тесты, mypy, etc).
"""
from __future__ import annotations

import io
import logging
from typing import Any

from PIL import Image

logger = logging.getLogger(__name__)

_session: Any = None  # ленивая инициализация


def _get_session() -> Any:
    global _session
    if _session is None:
        from rembg import new_session  # type: ignore[import-not-found]
        _session = new_session("u2net")
        logger.info("bg_remove: U2Net session initialized")
    return _session


def remove_bg(src_bytes: bytes) -> bytes:
    """Принимает байты исходного фото товара.

    Возвращает PNG-байты с прозрачным фоном (товар вырезан и обрезан по bbox).
    """
    # rembg.bg делает sys.exit(1) если onnxruntime не установлен — это валит
    # весь uvicorn-процесс через SystemExit (он не Exception, а BaseException).
    # Ловим явно и поднимаем RuntimeError, чтобы pipeline-handler смог его обработать.
    try:
        from rembg import remove  # type: ignore[import-not-found]
    except SystemExit as e:
        raise RuntimeError(
            "rembg недоступен (onnxruntime отсутствует). "
            "Установи 'rembg[cpu]' в requirements.txt и переустанови."
        ) from e

    src_img = Image.open(io.BytesIO(src_bytes)).convert("RGBA")
    cut = remove(src_img, session=_get_session(), post_process_mask=True)

    # Crop по bounding box непрозрачных пикселей — обрезаем лишний транспарент
    bbox = cut.getbbox()
    if bbox:
        cut = cut.crop(bbox)

    out = io.BytesIO()
    cut.save(out, format="PNG", optimize=True)
    out.seek(0)
    logger.info("bg_remove: cut size=%dx%d", cut.width, cut.height)
    return out.getvalue()
