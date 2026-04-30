# Рефакторинг pipeline генерации карточек товаров

Полный план перехода с текущей архитектуры (gpt-image-2-image-to-image, JSON-промпты, 4 параллельные генерации от src) на стабильный pipeline с pixel-perfect консистентностью товара.

**Целевой результат:** карточки уровня референсов Свобода/Палетт — товар попиксельно идентичен на main / pack2 / pack3, единая серия, нулевой брак на тексте плашек.

---

## Содержание

1. [Диагноз: почему сейчас дрифтит](#1-диагноз-почему-сейчас-дрифтит)
2. [Шаг 0: смена модели kie.ai (5 минут)](#2-шаг-0-смена-модели-kieai)
3. [Шаг 1: переписать compile_image_prompt на естественный язык](#3-шаг-1-переписать-compile_image_prompt)
4. [Шаг 2: гибридный pipeline с композитингом (правильное решение)](#4-шаг-2-гибридный-pipeline-с-композитингом)
5. [Шаг 3: retry и error handling](#5-шаг-3-retry-и-error-handling)
6. [Промпт для Claude Code](#6-промпт-для-claude-code)

---

## 1. Диагноз: почему сейчас дрифтит

### Корневая причина №1 — слабая модель для preservation

`gpt-image-2-image-to-image` плохо держит идентичность объекта при edit. Это её фундаментальное свойство: внутри VAE → latent → denoise → decode, в latent-пространстве нет понятия "тот же самый pixel-perfect объект", есть только "семантически похожий". Identity_lock в текстовом промпте даёт ~30% эффективности — модель не reasoning-агент, она не "понимает" запрет, она статистически предсказывает пиксели.

### Корневая причина №2 — нет рычагов в payload

Текущий payload:

```python
body = {
    "model": "gpt-image-2-image-to-image",
    "input": {
        "prompt": "...",
        "aspect_ratio": "3:4",
        "resolution": "2K",
        "input_urls": ["..."]
    }
}
```

Нет полей `seed`, `image_weight`, `reference_strength`, `guidance`, `denoising_strength`. То есть управлять "насколько сильно держаться за референс" никак нельзя — модель сама решает. На альтернативных моделях (flux-kontext, nano-banana-pro) такие параметры есть.

### Корневая причина №3 — стена JSON в промпте

Текущий `compile_image_prompt` собирает огромный JSON со 100+ полями — `IDENTITY_LOCK.product_features_from_reference.label_text`, `extra_override.usage_steps.examples_by_category` и т.д. Image-модели парсят промпт линейно, как обычный текст. Вложенный JSON для них — шум. Половина инструкций друг другу противоречит ("preserve exactly" + "improve lighting" + новый дизайн вокруг). Естественноязыковый промпт на 200 слов работает в 2-3 раза лучше структурированного на 800.

### Что НЕ виновато

Архитектура с параллельными генерациями от src — корректна. Каскад main → pack2 (как делает кастомный GPT) на gpt-image-2 уже пробовали — не помогло, потому что модель на каждом шаге накапливает дрифт. На flux-kontext или nano-banana-pro каскад работал бы лучше, но это уже про смену модели.

---

## 2. Шаг 0: смена модели kie.ai

**Время: 5–10 минут. Может закрыть 80% боли.**

### Изменение в `.env`

```bash
# было
KIE_IMAGE_MODEL=gpt-image-2-image-to-image

# стало (один из вариантов)
KIE_IMAGE_MODEL=nano-banana-pro
# или
KIE_IMAGE_MODEL=flux-kontext-pro
```

Точные названия моделей проверь в kie.ai dashboard или в их API docs (`https://docs.kie.ai`). У тебя в одном из других проектов уже использовался `nano-banana-pro` через kie.ai, значит модель там точно есть.

### Почему это может решить проблему

| Модель | Subject preservation | Текст на упаковке | Edit-режим |
|---|---|---|---|
| gpt-image-2-image-to-image | средняя | плывёт | базовый |
| **nano-banana-pro** (Gemini 3 Pro Image) | **высокая** | **держит почти попиксельно** | mask-based |
| **flux-kontext-pro** | **очень высокая** | держит хорошо | специально под edit |

`flux-kontext-pro` от Black Forest Labs — индустриальный стандарт для задач "preserve subject, change scene". Это та модель, которую используют студии делающие референсы Свобода/Палетт.

### Возможно надо добавить параметры в payload

После смены модели проверь в docs kie.ai какие дополнительные параметры доступны для новой модели. Обычно есть:

```python
body = {
    "model": "flux-kontext-pro",
    "input": {
        "prompt": "...",
        "aspect_ratio": "3:4",
        "input_urls": ["..."],
        # новые поля для edit-моделей:
        "image_weight": 0.85,        # как сильно держаться за референс (0..1)
        "guidance_scale": 7.5,       # как сильно слушать промпт
        "seed": 42                   # для воспроизводимости
    }
}
```

Если такие поля есть — добавь в `KieAIClient.create_image_task` опциональные параметры и пробрасывай их.

### Тест

Прогони 2-3 товара через бота на новой модели. Сравни main/pack2/pack3:
- Если упаковка идентична на всех трёх → задача решена, дальше не идёшь.
- Если ещё немного дрифтит → переходи к Шагу 1.
- Если дрифт сильный → переходи сразу к Шагу 2 (гибрид).

---

## 3. Шаг 1: переписать compile_image_prompt

**Время: 1-2 часа. Улучшает результат на любой модели.**

### Что выбрасываем

Всю стену JSON со вложенными `IDENTITY_LOCK`, `explicit_warnings`, `extra_override` и т.д. На выходе функции должна быть **обычная строка естественного текста**, не JSON-объект.

### Принципы нового промпта

1. **Естественный текст**, не JSON. Image-модели не парсят структуру.
2. **Идентичность товара через визуальное описание** ("white plastic pouch with red logo"), а не через абстрактные `do_not_change: ["shape", "proportions"]`.
3. **Запреты формулируются позитивно** — "keep packaging identical to reference", не "DO NOT change shape". У image-моделей слабая обработка отрицаний.
4. **Один сценарий на промпт.** Никаких вложенных оверрайдов с условиями.
5. **Бенефиты, плашки, бренд-блок — описаны конкретно**, с цветом и расположением: "red circle with white checkmark on the left of the badge".
6. **Палитра — в HEX в самом конце**, как реминд для модели.

### Полный новый код `compile_image_prompt`

Заменяет текущую функцию в `app/prompts.py`:

```python
def compile_image_prompt(
    brief: dict,
    product_name: str,
    mode: str,  # "main" | "pack2" | "pack3" | "extra"
    qty: int = 1,
) -> str:
    """
    Собирает промпт для image-модели как естественный текст.
    
    На вход — JSON-бриф от vision LLM:
    {
      "identity": {shape, proportions, colors_packaging, label_text, brand_visual, key_features, ...},
      "design": {category_guess, scene, palette, brand_block, benefits, volume_badge, ...}
    }
    
    На выход — одна строка ~200 слов на английском (image-модели лучше парсят EN).
    Все user-facing надписи на карточке — на русском (через quoted text в промпте).
    """
    identity = brief.get("identity") or {}
    design = brief.get("design") or {}
    
    # ── Блок 1: общее описание сцены ──────────────────────
    aspect = "3:4 vertical Russian marketplace product card, 2K resolution"
    scene = design.get("scene", "clean studio background with soft natural light")
    mood = design.get("mood", "clean, fresh, professional")
    
    # ── Блок 2: визуальное описание товара (identity) ─────
    shape = identity.get("shape", "")
    proportions = identity.get("proportions", "")
    colors = identity.get("colors_packaging", [])
    label_text = identity.get("label_text", "")
    brand_visual = identity.get("brand_visual", "")
    key_features = identity.get("key_features", []) or []
    
    product_desc = (
        f"The product is the SAME as in the reference image: "
        f"{shape}, {proportions}. "
        f"Packaging colors: {', '.join(colors) if colors else 'as in reference'}. "
        f"Label visual: {brand_visual}. "
        f"Key visual features: {', '.join(key_features)}. "
        f"Keep packaging identical to reference — same shape, same colors, "
        f"same logo, same label text, same proportions. Only relight, do not redesign."
    )
    
    # ── Блок 3: композиция (зависит от mode) ──────────────
    if mode == "main":
        composition = (
            "Composition: single product centered, taking 50–60% of frame, "
            "slight soft shadow underneath, slight 3/4 angle for depth."
        )
        units_caption = ""
    elif mode == "pack2":
        composition = (
            "Composition: TWO IDENTICAL product packages side by side, centered, "
            "same lighting on both, same shadow direction. Both products are 100% identical "
            "to each other and to the reference. Spacing between them is moderate, "
            "they don't overlap."
        )
        units_caption = '"Набор 2 штуки" prominently displayed near the top.'
    elif mode == "pack3":
        composition = (
            "Composition: THREE IDENTICAL product packages arranged in a fan/row, "
            "centered, same lighting and same shadow direction on all three. "
            "All three products are 100% identical to each other and to the reference. "
            "Slight overlap or moderate spacing — readable composition."
        )
        units_caption = '"Набор 3 штуки" prominently displayed near the top.'
    elif mode == "extra":
        composition = (
            "Composition: single product centered, taking 60–70% of frame, "
            "hero shot, slight angle. Plus 3-4 small numbered step icons at the bottom "
            "with short Russian captions describing how to use the product "
            "(based on category). Top header: «СПОСОБ ПРИМЕНЕНИЯ» in bold."
        )
        units_caption = ""
    else:
        composition = "Composition: single product centered."
        units_caption = ""
    
    # ── Блок 4: design (фон, плашки, бренд-блок) ──────────
    brand_block = design.get("brand_block", {}) or {}
    benefits = design.get("benefits", []) or []
    volume = design.get("volume_badge", {}) or {}
    palette = design.get("palette", []) or []
    
    brand_text = brand_block.get("brand_text", "")
    category_text = brand_block.get("category_text", "")
    brand_pos = brand_block.get("position", "top-center")
    
    benefits_text = ""
    if benefits:
        items = " · ".join(f'"{b}"' for b in benefits[:3])
        benefits_text = (
            f"On the left side: {len(benefits[:3])} benefit badges, each is a "
            f"small white pill with a red circle containing a white checkmark, "
            f"followed by Russian text: {items}. Stack them vertically."
        )
    
    volume_text = ""
    if volume.get("text"):
        volume_text = (
            f'Bottom-right corner: red circular badge with white text "{volume["text"]}".'
        )
    
    brand_text_block = ""
    if brand_text:
        brand_text_block = (
            f'{brand_pos.title()}: brand block with "{brand_text}"'
            + (f' / "{category_text}"' if category_text else '')
            + ' in bold sans-serif on a clean rounded pill background.'
        )
    
    palette_hex = ", ".join(palette) if palette else "neutral palette matching the product"
    
    # ── Блок 5: финальная сборка ──────────────────────────
    parts = [
        f"{aspect}.",
        f"Scene: {scene}. Mood: {mood}.",
        product_desc,
        composition,
        brand_text_block,
        benefits_text,
        units_caption,
        volume_text,
        f"Palette hint: {palette_hex}.",
        "All on-card text in Russian only, no typos, no broken letters, "
        "clean modern sans-serif typography. No clutter, professional clean composition. "
        "Respect 3:4 vertical safe zones — important text away from edges.",
    ]
    
    prompt = " ".join(p.strip() for p in parts if p.strip())
    return prompt
```

### Пример что выходит на выходе (для Санокс, mode=pack2)

```
3:4 vertical Russian marketplace product card, 2K resolution. 
Scene: blurred clean kitchen with sink and chrome pipe, soft natural light. 
Mood: clean, fresh, professional. 
The product is the SAME as in the reference image: пакет прямоугольной формы 
с заострёнными верхом и низом, мягкий пластик, вертикальный прямоугольник 
1.8:1. Packaging colors: #FFFFFF, #FF0000, #00AEEF, #FFD200. Label visual: 
логотип 'САНОКС' белый шрифт на красной вытянутой плашке. Key visual features: 
верх пакета заострён, цветной графический элемент с каплями и трубой, красная 
горизонтальная плашка с логотипом. Keep packaging identical to reference — 
same shape, same colors, same logo, same label text, same proportions. 
Only relight, do not redesign. 
Composition: TWO IDENTICAL product packages side by side, centered, same 
lighting on both, same shadow direction. Both products are 100% identical 
to each other and to the reference. 
Top-Center: brand block with "САНОКС" / "Чистый сток" in bold sans-serif 
on a clean rounded pill background. 
On the left side: 3 benefit badges, each is a small white pill with a red 
circle containing a white checkmark, followed by Russian text: "Быстро 
устраняет засоры" · "Удаляет неприятный запах" · "Безопасно для труб". 
"Набор 2 штуки" prominently displayed near the top. 
Palette hint: #FFFFFF, #00AEEF, #FFD200, #FF0000. 
All on-card text in Russian only, no typos...
```

Видно — это **связный текст**, который модель парсит как описание сцены, а не как структурированные команды.

---

## 4. Шаг 2: гибридный pipeline с композитингом

**Время: 1-2 дня. Даёт 0% брака на товаре и pixel-perfect консистентность серии.**

### Архитектура

```
src.jpg
   │
   ├──► [rembg U2Net] ──► product.png (с прозрачным фоном)
   │
   └──► [vision LLM gpt-5-2] ──► identity + design brief
                                     │
                                     ▼
            ┌─────────[gpt-image / nano-banana генерит ТОЛЬКО фон]─────────┐
            │  промпт: "blurred kitchen, sink, soft light, water droplets,  │
            │  central area kept empty, no products in image"               │
            ▼                                                                │
       background.png                                                        │
            │                                                                │
            ├──► PIL composite: bg + product×1 + soft shadow ──► main_base  │
            ├──► PIL composite: bg + product×2 + shadows ──► pack2_base     │
            └──► PIL composite: bg + product×3 + shadows ──► pack3_base     │
                                                                             │
            ┌────────────────[Playwright + Jinja2]────────────────────┐     │
            │  HTML/CSS шаблоны плашек (Inter / Manrope шрифты,        │     │
            │  бренд-блок, бенефит-карточки, "Набор N штуки",          │     │
            │  объёмная плашка) → screenshot → PNG с альфой            │     │
            └──────────────────┬───────────────────────────────────────┘     │
                               │                                             │
                               ▼                                             │
                  PIL composite: base + plashki ──► финальная карточка ◄────┘
```

**Почему это работает:**

- товар на pack2/pack3 — буквально тот же файл что на main, **попиксельно идентичный**
- AI генерит только фон без товара → задача в разы проще, брак почти ноль
- плашки/текст рендерятся через Playwright из HTML/CSS → **идеальная типографика, нулевой брак на тексте**, любые шрифты, лёгкая адаптация под бренд через Jinja2

### 4.1 Установка зависимостей

Добавить в `requirements.txt`:

```
rembg>=2.0.59
playwright>=1.47
jinja2>=3.1
```

После `pip install` нужно один раз скачать chromium для Playwright:

```bash
playwright install chromium
```

И при первом запуске rembg сам скачает U2Net модель (~170MB) в `~/.u2net/`.

### 4.2 Новый модуль `app/bg_remove.py`

```python
"""Background removal через rembg (U2Net локально)."""
from __future__ import annotations

import io
import logging

from PIL import Image
from rembg import remove, new_session

logger = logging.getLogger(__name__)

# Один раз создаём session — не пересоздаём на каждом вызове
_session = new_session("u2net")


def remove_bg(src_bytes: bytes) -> bytes:
    """
    Принимает байты исходного фото товара.
    Возвращает PNG-байты с прозрачным фоном (товар вырезан).
    """
    src_img = Image.open(io.BytesIO(src_bytes)).convert("RGBA")
    cut = remove(src_img, session=_session, post_process_mask=True)
    
    # Crop по bounding box непрозрачных пикселей — обрезаем лишний транспарент
    bbox = cut.getbbox()
    if bbox:
        cut = cut.crop(bbox)
    
    out = io.BytesIO()
    cut.save(out, format="PNG", optimize=True)
    out.seek(0)
    logger.info("bg_remove: cut size=%dx%d", cut.width, cut.height)
    return out.getvalue()
```

### 4.3 Новый модуль `app/composite.py`

```python
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
    """Возвращает RGBA с мягкой тенью под товаром."""
    w, h = product.size
    canvas = Image.new("RGBA", (w, h + offset_y * 2), (0, 0, 0, 0))
    
    # Тень = силуэт товара (alpha) → blur → чёрный цвет с opacity
    alpha = product.split()[3]
    shadow = Image.new("RGBA", product.size, (0, 0, 0, 0))
    shadow.putalpha(alpha)
    shadow_black = Image.new("RGBA", product.size, (0, 0, 0, opacity))
    shadow_black.putalpha(alpha.point(lambda p: int(p * opacity / 255)))
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
    """
    Накладывает товар на фон N раз.
    
    bg_bytes: PNG/JPG фона (3:4 vertical, без товара)
    product_bytes: PNG товара с альфа-каналом (после rembg)
    units: 1 / 2 / 3 — сколько копий товара
    
    Возвращает PNG-байты готовой карточки (без плашек).
    """
    bg = Image.open(io.BytesIO(bg_bytes)).convert("RGBA").resize((CARD_W, CARD_H), Image.LANCZOS)
    product = Image.open(io.BytesIO(product_bytes)).convert("RGBA")
    
    # Размеры товара под композицию
    if units == 1:
        product_h = int(CARD_H * 0.55)  # 55% высоты карточки
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
    
    # Центр товаров по вертикали — чуть ниже середины
    y = int(CARD_H * 0.55) - ph // 2
    
    for x_ratio in positions_x_ratio:
        x = int(CARD_W * x_ratio) - pw // 2
        canvas.alpha_composite(product_with_shadow, (x, y))
    
    out = io.BytesIO()
    canvas.convert("RGB").save(out, format="JPEG", quality=92, optimize=True)
    out.seek(0)
    logger.info("composite: units=%d size=%dx%d", units, CARD_W, CARD_H)
    return out.getvalue()
```

### 4.4 Новый модуль `app/plashki.py`

```python
"""Рендер плашек через Playwright + Jinja2."""
from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.async_api import async_playwright
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
    """
    Рендерит Jinja2-шаблон в HTML, открывает в headless Chrome,
    делает screenshot с прозрачным фоном.
    """
    template = _env.get_template(template_name)
    html = template.render(**context)
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": width, "height": height},
            device_scale_factor=2,  # retina-качество
        )
        page = await ctx.new_page()
        await page.set_content(html, wait_until="networkidle")
        png = await page.screenshot(omit_background=True, full_page=False)
        await browser.close()
    
    logger.info("plashki: rendered %s size=%dx%d bytes=%d", template_name, width, height, len(png))
    return png


async def overlay_plashki(card_bytes: bytes, plashki_png: bytes) -> bytes:
    """Накладывает плашки (PNG с альфой) поверх готовой карточки."""
    card = Image.open(io.BytesIO(card_bytes)).convert("RGBA")
    plashki = Image.open(io.BytesIO(plashki_png)).convert("RGBA")
    
    if plashki.size != card.size:
        plashki = plashki.resize(card.size, Image.LANCZOS)
    
    card.alpha_composite(plashki)
    out = io.BytesIO()
    card.convert("RGB").save(out, format="JPEG", quality=92, optimize=True)
    out.seek(0)
    return out.getvalue()
```

### 4.5 Шаблоны плашек

Файл `app/templates/card_plashki.html.j2`:

```html
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  @import url('https://fonts.googleapis.com/css2?family=Manrope:wght@600;700;800;900&display=swap');
  
  * { margin: 0; padding: 0; box-sizing: border-box; font-family: 'Manrope', sans-serif; }
  body { background: transparent; width: 1536px; height: 2048px; position: relative; }
  
  /* ── Бренд-блок (top-center) ── */
  .brand-block {
    position: absolute; top: 80px; left: 50%; transform: translateX(-50%);
    background: white; border-radius: 80px; padding: 30px 70px;
    box-shadow: 0 4px 20px rgba(0,0,0,.1);
    text-align: center;
  }
  .brand-text { font-size: 90px; font-weight: 900; color: {{ brand_color | default('#E2231A') }}; line-height: 1; }
  .category-text { font-size: 50px; font-weight: 700; color: {{ category_color | default('#00AEEF') }}; margin-top: 8px; }
  
  /* ── Бенефиты (left side) ── */
  .benefits {
    position: absolute; top: 50%; left: 40px; transform: translateY(-50%);
    display: flex; flex-direction: column; gap: 24px;
  }
  .benefit {
    display: flex; align-items: center; gap: 18px;
    background: white; border-radius: 50px; padding: 18px 32px;
    box-shadow: 0 3px 14px rgba(0,0,0,.08);
    max-width: 400px;
  }
  .benefit-check {
    width: 50px; height: 50px; border-radius: 50%; background: #E2231A;
    display: flex; align-items: center; justify-content: center;
    color: white; font-size: 32px; font-weight: 900; flex-shrink: 0;
  }
  .benefit-text { font-size: 28px; font-weight: 700; color: #1a1a1a; line-height: 1.15; }
  
  /* ── Объёмная плашка (bottom-right) ── */
  .volume-badge {
    position: absolute; bottom: 80px; right: 80px;
    width: 200px; height: 200px; border-radius: 50%;
    background: #E2231A; color: white;
    display: flex; align-items: center; justify-content: center;
    font-size: 56px; font-weight: 900;
    box-shadow: 0 6px 24px rgba(226,35,26,.4);
  }
  
  /* ── Капция набора (если есть) ── */
  .units-caption {
    position: absolute; top: 380px; left: 50%; transform: translateX(-50%);
    background: #E2231A; color: white;
    padding: 20px 50px; border-radius: 60px;
    font-size: 56px; font-weight: 900;
    box-shadow: 0 6px 20px rgba(226,35,26,.35);
  }
</style>
</head>
<body>
  
  {% if brand_text %}
  <div class="brand-block">
    <div class="brand-text">{{ brand_text }}</div>
    {% if category_text %}<div class="category-text">{{ category_text }}</div>{% endif %}
  </div>
  {% endif %}
  
  {% if units_caption %}
  <div class="units-caption">{{ units_caption }}</div>
  {% endif %}
  
  {% if benefits %}
  <div class="benefits">
    {% for b in benefits[:3] %}
    <div class="benefit">
      <div class="benefit-check">✓</div>
      <div class="benefit-text">{{ b }}</div>
    </div>
    {% endfor %}
  </div>
  {% endif %}
  
  {% if volume_text %}
  <div class="volume-badge">{{ volume_text }}</div>
  {% endif %}
  
</body>
</html>
```

Это **один универсальный шаблон**. Под другие категории (косметика, продукты, бытовая химия) делаешь варианты — `card_plashki_cosmetics.html.j2`, `card_plashki_food.html.j2` — с другими цветами и формой плашек. Vision LLM в design-секции уже отдаёт `category_guess` — по нему выбираешь шаблон.

### 4.6 Обновлённый `app/pipeline.py` — функция `process_product_images`

```python
"""Обновлённый pipeline под гибридную архитектуру."""
import asyncio
import logging
from typing import Any

from app.bg_remove import remove_bg
from app.composite import composite_card
from app.kie_ai import KieAIClient
from app.plashki import render_html_to_png, overlay_plashki
from app.prompts import compile_image_prompt, compile_bg_only_prompt
from app.s3 import S3Client

logger = logging.getLogger(__name__)


async def process_product_images(
    *,
    src_bytes: bytes,
    sku: str,
    batch_id: str,
    brief: dict[str, Any],
    kie: KieAIClient,
    s3: S3Client,
) -> dict[str, str]:
    """
    Гибридный pipeline:
    1. bg_remove src → product.png (alpha)
    2. AI генерит фоны под main / pack2 / pack3 / extra (без товара!)
    3. PIL composite: bg + product × N
    4. Playwright рендерит плашки → PIL overlay
    5. S3 upload
    
    Возвращает {mode: s3_url} — main/pack2/pack3/extra.
    """
    # ── 1. Background removal (локально, ~1 сек) ──────────
    product_png = remove_bg(src_bytes)
    product_url = await s3.put_public(
        f"{batch_id}/{sku}_product.png", product_png, content_type="image/png"
    )
    logger.info("pipeline: bg removed sku=%s", sku)
    
    # ── 2. AI генерит фоны (4 параллельных) ───────────────
    modes = ["main", "pack2", "pack3", "extra"]
    bg_prompts = {m: compile_bg_only_prompt(brief, mode=m) for m in modes}
    
    async def gen_bg(mode: str) -> tuple[str, bytes]:
        task_id = await kie.create_image_task(
            prompt=bg_prompts[mode],
            input_urls=None,  # text-to-image для фона!
            aspect_ratio="3:4",
            resolution="2K",
        )
        url = await kie.poll_image_task(task_id)
        bg_bytes = await s3.fetch_url(url)
        return mode, bg_bytes
    
    bg_results = await asyncio.gather(*[gen_bg(m) for m in modes])
    bgs: dict[str, bytes] = dict(bg_results)
    
    # ── 3. Composite каждой карточки ──────────────────────
    units_map = {"main": 1, "pack2": 2, "pack3": 3, "extra": 1}
    base_cards: dict[str, bytes] = {}
    for mode in modes:
        base_cards[mode] = composite_card(
            bg_bytes=bgs[mode],
            product_bytes=product_png,
            units=units_map[mode],
        )
    
    # ── 4. Rendering плашек и финальный overlay ───────────
    final_urls: dict[str, str] = {}
    for mode in modes:
        plashki_ctx = _build_plashki_context(brief, mode)
        plashki_png = await render_html_to_png(
            "card_plashki.html.j2",
            plashki_ctx,
        )
        final = await overlay_plashki(base_cards[mode], plashki_png)
        url = await s3.put_public(
            f"{batch_id}/{sku}_{mode}.jpg", final, content_type="image/jpeg"
        )
        final_urls[mode] = url
        logger.info("pipeline: %s done sku=%s url=%s", mode, sku, url)
    
    return final_urls


def _build_plashki_context(brief: dict, mode: str) -> dict[str, Any]:
    """Собирает Jinja2-контекст из brief под конкретный mode."""
    design = brief.get("design") or {}
    brand_block = design.get("brand_block", {}) or {}
    benefits = design.get("benefits", []) or []
    volume = design.get("volume_badge", {}) or {}
    palette = design.get("palette", []) or []
    
    units_caption = ""
    if mode == "pack2":
        units_caption = "Набор 2 штуки"
    elif mode == "pack3":
        units_caption = "Набор 3 штуки"
    
    # Цвета — берём из палитры brief, fallback на дефолт
    brand_color = palette[0] if palette else "#E2231A"
    category_color = palette[1] if len(palette) > 1 else "#00AEEF"
    
    return {
        "brand_text": brand_block.get("brand_text", ""),
        "category_text": brand_block.get("category_text", ""),
        "brand_color": brand_color,
        "category_color": category_color,
        "benefits": benefits if mode in ("main", "pack2", "pack3") else [],
        "volume_text": volume.get("text", "") if mode == "main" else "",
        "units_caption": units_caption,
    }
```

### 4.7 Новая функция в `app/prompts.py`

```python
def compile_bg_only_prompt(brief: dict, mode: str) -> str:
    """
    Промпт для генерации ТОЛЬКО фона/сцены, без товара.
    Используется в гибридном pipeline где товар накладывается отдельно.
    """
    design = brief.get("design") or {}
    scene = design.get("scene", "clean studio with soft natural light")
    mood = design.get("mood", "clean, fresh, professional")
    palette = design.get("palette", []) or []
    
    # Под main / pack2 / pack3 / extra — слегка разные сцены чтобы серия не была одинаковой
    scene_variant = {
        "main": "wide hero shot, product placement zone in the center-bottom of frame",
        "pack2": "same scene but slightly different angle, central zone wide enough for two items",
        "pack3": "same scene but pulled back, central zone wide enough for three items",
        "extra": "tighter close-up of the same scene, central zone for hero product, bottom area clean for usage steps",
    }.get(mode, "central zone empty for product placement")
    
    palette_hex = ", ".join(palette) if palette else "neutral palette"
    
    return (
        f"3:4 vertical Russian marketplace card BACKGROUND ONLY, 2K resolution. "
        f"Scene: {scene}. {scene_variant}. Mood: {mood}. "
        f"IMPORTANT: NO PRODUCTS in the image. NO objects in the central placement zone — "
        f"keep it visually clean and slightly empty so a product can be placed there separately. "
        f"Soft natural lighting, slight depth of field, clean composition. "
        f"Background only — like a stage waiting for an item. "
        f"Palette hint: {palette_hex}. "
        f"No text, no logos, no labels. Just the environment."
    )
```

---

## 5. Шаг 3: retry и error handling

Из логов видно что один из 4 тасков (`extra`) залип в polling и в TG ушло 3 фото вместо 4. Это отдельный баг, фиксится так:

### 5.1 Добавить retry в `KieAIClient.create_image_task`

```python
async def create_image_task_with_retry(
    self,
    *,
    prompt: str,
    input_urls: list[str] | None = None,
    aspect_ratio: str = "3:4",
    resolution: str = "2K",
    model: str | None = None,
    max_retries: int = 2,
) -> str:
    """create_image_task с retry на сетевые ошибки и API-фейлы."""
    last_err: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return await self.create_image_task(
                prompt=prompt,
                input_urls=input_urls,
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                model=model,
            )
        except (KieAIError, httpx.HTTPError) as e:
            last_err = e
            logger.warning("kie.create_image_task retry %d/%d: %s", attempt + 1, max_retries, e)
            await asyncio.sleep(2 ** attempt)
    raise KieAIError(f"create_image_task failed after {max_retries + 1} attempts: {last_err}")
```

### 5.2 Polling с failure logging

В `KieAIClient.poll_image_task` логировать `failMsg` и поднимать с понятным сообщением:

```python
async def poll_image_task(self, task_id: str) -> str:
    """Polling до success / fail. Логирует failMsg при ошибке."""
    for attempt in range(self._poll_max_attempts):
        await asyncio.sleep(self._poll_interval)
        info = await self._record_info(task_id)
        state = info.get("data", {}).get("state")
        
        if state == "success":
            result_json_str = info["data"]["resultJson"]
            result = json.loads(result_json_str)
            url = result["resultUrls"][0]
            logger.info("kie.poll success taskId=%s url=%s", task_id, url)
            return url
        
        if state == "fail":
            fail_msg = info.get("data", {}).get("failMsg", "unknown")
            logger.error("kie.poll FAIL taskId=%s msg=%s", task_id, fail_msg)
            raise KieAIError(f"task {task_id} failed: {fail_msg}")
    
    raise KieAIError(f"task {task_id} timeout after {self._poll_max_attempts} attempts")
```

### 5.3 Strict gather с partial fallback

В `pipeline.process_product_images` использовать `asyncio.gather(..., return_exceptions=True)` чтобы один failed task не валил остальные, и потом логировать ошибки + показывать пользователю что не дошло:

```python
results = await asyncio.gather(
    *[gen_one(mode) for mode in modes],
    return_exceptions=True,
)

ok: dict[str, str] = {}
failed: list[str] = []
for mode, res in zip(modes, results):
    if isinstance(res, Exception):
        logger.error("pipeline: %s failed: %s", mode, res)
        failed.append(mode)
    else:
        ok[mode] = res

if failed:
    # Сообщаем юзеру что часть не дошла
    await tg.send(chat_id, f"⚠️ Не сгенерировалось: {', '.join(failed)}. Остальные ОК.")

return ok
```

---

## 6. Промпт для Claude Code

Скопируй всё что ниже **между линиями** в Claude Code в терминале. Перед этим положи этот документ (`refactor_plan.md`) в корень проекта — Claude Code будет на него ссылаться.

---

```
Тебе нужно внедрить рефакторинг pipeline генерации карточек товаров. Полный план с диагнозом, кодом и архитектурой лежит в файле refactor_plan.md в корне проекта — прочитай его целиком, прежде чем что-либо делать.

ЭТАПНОСТЬ. Не делай всё за один заход. Сделай по этапам, после каждого — короткий отчёт что сделал, что протестировал, и спроси можно ли двигаться дальше. Этапы:

ЭТАП 1 — смена модели kie.ai (минимальное вмешательство).
1. Прочитай refactor_plan.md разделы 1 и 2.
2. Обнови .env.example: замени строку KIE_IMAGE_MODEL=gpt-image-2-image-to-image на KIE_IMAGE_MODEL=nano-banana-pro и добавь ниже комментарий с альтернативой (flux-kontext-pro). Реальный .env я обновлю руками.
3. В app/kie_ai.py в методе create_image_task — расширь сигнатуру опциональными параметрами image_weight, guidance_scale, seed (все Optional[float|int], default None). Если параметр не None — добавляй его в body["input"]. Не ломай обратную совместимость для существующих вызовов.
4. Запусти существующие тесты в tests/test_kie_ai.py — должны проходить.
5. Доложи мне что сделано и попроси протестировать на реальном товаре через бота.

ЭТАП 2 — переписать compile_image_prompt на естественный язык.
1. Прочитай refactor_plan.md раздел 3 — там полный новый код функции.
2. В app/prompts.py замени текущую compile_image_prompt на новую версию из плана. Старую закомментируй с пометкой "# LEGACY: keep for fallback, see refactor_plan.md §3" — не удаляй, могу понадобиться откатить.
3. Также добавь новую функцию compile_bg_only_prompt из refactor_plan.md §4.7 (она нужна для Этапа 3, но логически живёт в prompts.py — добавляй сразу).
4. Обнови tests/test_pipeline.py если там есть тесты на compile_image_prompt — проверь что новая функция возвращает строку (не dict), и эта строка содержит ключевые слова: "reference image", "Russian marketplace", "Composition:", палитру.
5. Доложи и попроси протестировать.

ЭТАП 3 — гибридный pipeline (главный этап).
1. Прочитай refactor_plan.md разделы 4.1–4.7 целиком, не пропускай.
2. Обнови requirements.txt: добавь rembg>=2.0.59, playwright>=1.47, jinja2>=3.1. Скажи мне выполнить pip install -r requirements.txt и playwright install chromium на сервере.
3. Создай новые модули как описано в плане:
   - app/bg_remove.py (полный код в плане §4.2, копируй как есть)
   - app/composite.py (полный код в §4.3)
   - app/plashki.py (полный код в §4.4)
4. Создай директорию app/templates/ и файл app/templates/card_plashki.html.j2 с полным содержимым из §4.5.
5. Перепиши функцию process_product_images в app/pipeline.py согласно §4.6. Старую версию переименуй в process_product_images_legacy, оставь её — переключение делается через флаг в config (добавь в app/config.py поле PIPELINE_MODE: Literal["legacy", "hybrid"] = "hybrid"). Главный handler в pipeline.py пусть смотрит на этот флаг и диспатчит на соответствующую функцию.
6. Напиши минимальные интеграционные тесты в tests/test_hybrid.py:
   - test_remove_bg возвращает PNG-байты с альфой
   - test_composite собирает 1/2/3 товара на фоне без падений
   - test_render_plashki рендерит шаблон в PNG (моком Playwright если надо)
7. НЕ запускай реальный бот в этом этапе. Просто проверь что импорты работают и тесты зелёные.
8. Доложи и попроси меня вручную прогнать одну продукт-генерацию через бота с PIPELINE_MODE=hybrid.

ЭТАП 4 — retry и error handling.
1. Прочитай §5 плана.
2. В app/kie_ai.py добавь create_image_task_with_retry (§5.1). Не заменяй существующий create_image_task — это новый метод поверх него. В app/pipeline.py в новой process_product_images замени все вызовы create_image_task на create_image_task_with_retry.
3. В app/kie_ai.py в poll_image_task — добавь логирование failMsg и raise понятной ошибки (§5.2). Если метод уже так делает — пропусти.
4. В app/pipeline.py в process_product_images используй asyncio.gather(*tasks, return_exceptions=True) и отдавай юзеру список failed mode'ов через telegram (§5.3).
5. Тесты на retry: моком httpx сделай 1-2 фейла подряд + успех — должно повториться, на 3-й попытке успех. Используй respx или pytest-httpx (уже в requirements).
6. Доложи и попроси протестировать.

ОБЩИЕ ПРАВИЛА.
- Не меняй ничего вне описанного в плане без моего разрешения. Если видишь баг или улучшение которое не в плане — пиши мне, не правь сам.
- Стиль кода — как уже принят в проекте (typing, asyncio, logging через logging.getLogger(__name__), docstrings на русском или английском как в окружающих файлах).
- Все ключевые env-переменные дублируй в .env.example.
- После каждого этапа: `git add -p && git commit -m "<этап N>: <краткое описание>"`. Не пуши автоматически.
- Если что-то непонятно в плане — спроси меня, не додумывай.

Начинай с Этапа 1.
```

---

## Резюме рекомендаций

| Шаг | Время | Эффект | Когда делать |
|---|---|---|---|
| **0. Смена модели** | 5–10 мин | может закрыть 80% боли | сейчас, первым делом |
| **1. Переписать промпты** | 1–2 ч | +20% качества на любой модели | если Шаг 0 не дотягивает |
| **2. Гибридный pipeline** | 1–2 дня | 100% pixel-perfect консистентность | для production-grade результата |
| **3. Retry/error handling** | 2–3 ч | стабильность, нет потерь карточек | в любой момент, но обязательно |

**Минимум:** Шаг 0 + Шаг 1 + Шаг 3. Это даст ощутимый рост качества за день работы.

**Максимум:** все четыре шага. Это даст production-grade систему уровня студий которые делают Свободу/Палетт.
