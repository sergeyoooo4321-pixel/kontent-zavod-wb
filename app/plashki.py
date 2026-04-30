"""Рендер плашек через Playwright + Jinja2.

HTML/CSS-шаблоны из app/templates/ → headless chromium screenshot с прозрачным фоном
→ overlay поверх готовой композитной карточки. Это даёт нулевой брак на тексте плашек
(шрифты, бренд-блок, бенефиты, объёмная плашка, "Набор N штуки") — pixel-perfect русский,
без артефактов image-моделей.
"""
from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)


async def render_html_to_png(
    template_name: str,
    context: dict[str, Any],
    width: int = 1536,
    height: int = 2048,
) -> bytes:
    """Рендерит Jinja2-шаблон в HTML, открывает в headless Chrome, screenshot с прозрачным фоном."""
    from playwright.async_api import async_playwright  # type: ignore[import-not-found]

    template = _env.get_template(template_name)
    html = template.render(**context)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": width, "height": height},
            device_scale_factor=2,
        )
        page = await ctx.new_page()
        await page.set_content(html, wait_until="networkidle")
        png = await page.screenshot(omit_background=True, full_page=False)
        await browser.close()

    logger.info("plashki: rendered %s size=%dx%d bytes=%d", template_name, width, height, len(png))
    return png


async def overlay_plashki(card_bytes: bytes, plashki_png: bytes) -> bytes:
    """Накладывает плашки (PNG с альфой) поверх готовой карточки. Возвращает JPEG."""
    card = Image.open(io.BytesIO(card_bytes)).convert("RGBA")
    plashki = Image.open(io.BytesIO(plashki_png)).convert("RGBA")

    if plashki.size != card.size:
        plashki = plashki.resize(card.size, Image.LANCZOS)

    card.alpha_composite(plashki)
    out = io.BytesIO()
    card.convert("RGB").save(out, format="JPEG", quality=92, optimize=True)
    out.seek(0)
    return out.getvalue()
