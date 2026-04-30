"""Композитинг товара поверх AI-фона. Pillow."""
from __future__ import annotations

import io
import logging
from typing import Literal

from PIL import Image, ImageFilter

logger = logging.getLogger(__name__)

CARD_W, CARD_H = 1536, 2048  # 3:4 vertical, 2K-ish


def _add_soft_shadow(
    product: Image.Image,
    blur: int = 30,
    offset_y: int = 20,
    opacity: int = 80,
) -> Image.Image:
    """Возвращает RGBA-канвас с мягкой тенью под товаром."""
    w, h = product.size
    canvas = Image.new("RGBA", (w, h + offset_y * 2), (0, 0, 0, 0))

    alpha = product.split()[3]
    shadow_alpha = alpha.point(lambda p: int(p * opacity / 255))
    shadow_black = Image.new("RGBA", product.size, (0, 0, 0, 0))
    shadow_black.putalpha(shadow_alpha)
    shadow_blur = shadow_black.filter(ImageFilter.GaussianBlur(blur))

    canvas.alpha_composite(shadow_blur, (0, offset_y))
    canvas.alpha_composite(product, (0, 0))
    return canvas


def _fit_product(product: Image.Image, max_h: int) -> Image.Image:
    """Масштабирует товар до max_h по высоте, сохраняя пропорции."""
    w, h = product.size
    if h <= max_h:
        return product
    new_w = int(w * max_h / h)
    return product.resize((new_w, max_h), Image.LANCZOS)


def composite_card(
    bg_bytes: bytes,
    product_bytes: bytes,
    units: Literal[1, 2, 3] = 1,
) -> bytes:
    """Накладывает товар на фон N раз (1/2/3 копии).

    bg_bytes: PNG/JPG фона (3:4 vertical, без товара)
    product_bytes: PNG товара с альфа-каналом (после rembg)
    units: 1 / 2 / 3 — сколько копий товара

    Возвращает JPEG-байты готовой карточки (без плашек).
    """
    bg = Image.open(io.BytesIO(bg_bytes)).convert("RGBA").resize(
        (CARD_W, CARD_H), Image.LANCZOS
    )
    product = Image.open(io.BytesIO(product_bytes)).convert("RGBA")

    if units == 1:
        product_h = int(CARD_H * 0.55)
        positions_x_ratio = [0.5]
    elif units == 2:
        product_h = int(CARD_H * 0.50)
        positions_x_ratio = [0.30, 0.70]
    elif units == 3:
        product_h = int(CARD_H * 0.45)
        positions_x_ratio = [0.22, 0.50, 0.78]
    else:
        raise ValueError(f"units must be 1/2/3, got {units}")

    product_fit = _fit_product(product, product_h)
    product_with_shadow = _add_soft_shadow(product_fit)
    pw, ph = product_with_shadow.size

    canvas = bg.copy()
    y = int(CARD_H * 0.55) - ph // 2

    for x_ratio in positions_x_ratio:
        x = int(CARD_W * x_ratio) - pw // 2
        canvas.alpha_composite(product_with_shadow, (x, y))

    out = io.BytesIO()
    canvas.convert("RGB").save(out, format="JPEG", quality=92, optimize=True)
    out.seek(0)
    logger.info("composite: units=%d size=%dx%d", units, CARD_W, CARD_H)
    return out.getvalue()
