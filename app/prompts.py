"""JSON-структурированные промпты для kie.ai.

Главное правило: design fixation. Для main — задаём строгую структуру дизайна.
Для pack/extra — указываем «копируй reference EXACTLY, меняй только X».
"""
from __future__ import annotations

import json


def _json(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


# ─── images ──────────────────────────────────────────────────


def build_main_prompt(product_name: str, brand: str | None = None) -> str:
    """Главное фото — задаёт ДИЗАЙН-ЭТАЛОН для всего комплекта товара.

    Жёсткая JSON-схема композиции, цветов, типографики.
    """
    brand_str = brand or "БРЕНД"
    spec = {
        "task": "marketplace product card image (Ozon/Wildberries style, premium russian retail)",
        "aspect_ratio": "3:4",
        "resolution": "2K",
        "language": "ТОЛЬКО РУССКИЙ ЯЗЫК на всех надписях. Никакого английского.",

        "design": {
            "style": "vibrant, high-contrast, modern marketplace card, sells the product at first glance",
            "background": (
                "smooth vibrant gradient or rich solid color matching product category. "
                "Examples: bright orange-yellow for hair products, deep brown-gold for coffee, "
                "fresh green for tea, soft pink for cosmetics. NO clutter, NO patterns."
            ),
            "product_placement": (
                "single product unit, centre-right, hero shot, slight 3/4 perspective, sharp focus, "
                "no distortion of packaging, real photo realism"
            ),
            "text_overlays": {
                "top_left_or_centre": {
                    "content": f"BRAND «{brand_str}»",
                    "style": "bold sans-serif, large, ALL CAPS, white or contrasting color"
                },
                "top_right_small": {
                    "content": "category tag",
                    "style": "small rounded badge"
                },
                "left_side_bullets": {
                    "content": "3 short benefits in pill-style bullets with tiny icons",
                    "style": "white text on semi-transparent dark pills, sans-serif"
                },
                "bottom_right_badge": {
                    "content": "weight or volume (e.g. «250 г», «1 л», «76 ₽»)",
                    "style": "circular or rounded badge, bright accent color"
                }
            },
            "typography": "modern Russian sans-serif (Inter, Manrope, Montserrat). NO orphan letters. NO misspelling.",
            "decorative_elements": "subtle: thin lines, small dots, gentle shapes — don't overwhelm"
        },

        "product": {
            "name": product_name,
            "language_of_packaging_text": "russian"
        },

        "constraints": [
            "ALL TEXT IS IN RUSSIAN ONLY",
            "no text errors, no broken letters, no Lorem Ipsum",
            "single product unit visible (NOT a pack of 2 or 3)",
            "no distortion of product packaging shape or label",
            "no watermarks, no logos other than the product brand",
            "russian text overlays only"
        ]
    }
    return _json(spec)


def build_pack_prompt(product_name: str, qty: int) -> str:
    """Карточка набора. ОБЯЗАТЕЛЬНО использовать main как input_url-референс,
    модель должна скопировать ВЕСЬ дизайн до пикселя.
    """
    assert qty in (2, 3)
    spec = {
        "task": (
            "EXACT replica of the reference image's design language, but showing a PACK of N units. "
            "Goal: looks like part of the SAME product line, indistinguishable design."
        ),
        "aspect_ratio": "3:4",
        "resolution": "2K",
        "language": "ТОЛЬКО РУССКИЙ. Никаких английских надписей.",

        "reference_role": (
            "the input_urls reference is the MAIN card of this product. "
            "Copy EVERY design element: gradient/background, fonts, color palette, "
            "logo block, badge style, decorative elements, layout grid."
        ),

        "must_be_identical_to_reference": [
            "background gradient/color",
            "brand block style and position",
            "all bullet/pill styles",
            "badge shape and palette",
            "typography (font, size, color)",
            "overall composition vibe"
        ],

        "differences_from_reference": {
            "product_count": qty,
            "caption_text": f"«Набор {qty} штуки»",
            "caption_position": "prominent — bottom-centre or replacing the volume badge",
            "product_arrangement": (
                f"{qty} identical units of the product, side by side or staggered, "
                f"in same hero-shot style as reference"
            )
        },

        "product": {
            "name": product_name,
            "qty": qty
        },

        "constraints": [
            "ALL TEXT IS IN RUSSIAN ONLY",
            "no text errors, no broken letters",
            f"exactly {qty} product units visible — not 1, not 4",
            "design palette MUST match reference",
            "no new design elements not present in reference",
            "preserve product packaging shape (no warping)"
        ]
    }
    return _json(spec)


def build_extra_prompt(product_name: str) -> str:
    """Инфографика-карточка. Тот же визуальный язык, но контент — преимущества/способ применения."""
    spec = {
        "task": (
            "infographic-style card that matches the visual design language of the reference image. "
            "Shows usage / composition / benefits, NOT a hero product photo."
        ),
        "aspect_ratio": "3:4",
        "resolution": "2K",
        "language": "ТОЛЬКО РУССКИЙ.",

        "reference_role": (
            "the input_urls reference is the MAIN card. "
            "Copy: background palette, fonts, badge style, decorative elements, brand block."
        ),

        "must_match_reference": [
            "color palette (gradient/solid)",
            "typography",
            "badge/pill design",
            "brand block style"
        ],

        "differences_from_reference": {
            "no_main_product_hero": "do not show the product as the centerpiece — it's an INFO card",
            "layout": "split into 3-4 visual blocks: each with an icon + short Russian caption",
            "content_options": [
                "usage steps (Шаг 1 / Шаг 2 / Шаг 3)",
                "composition icons (Состав: X, Y, Z)",
                "key benefits with icons (Преимущества: ...)",
                "any combination above — choose what fits the product"
            ],
            "small_product_thumbnail": "OK to include a small thumbnail of the product, but not as hero"
        },

        "product": {"name": product_name},

        "constraints": [
            "ALL TEXT IS IN RUSSIAN ONLY",
            "no text errors, no broken letters",
            "match reference visual design language",
            "infographic feel — NOT a sales hero shot"
        ]
    }
    return _json(spec)


# ─── LLM prompts (категории, тайтлы) — без изменений ────────────────


def build_category_prompts(
    product_name: str,
    ozon_leaves: list[dict],
    wb_leaves: list[dict],
    *,
    leaves_limit: int = 1500,
) -> tuple[str, str]:
    """Возвращает (system, user) для подбора категории Ozon+WB."""
    system = (
        "Ты — эксперт по каталогам маркетплейсов Ozon и Wildberries. "
        "По названию товара выбери НАИБОЛЕЕ ПОДХОДЯЩУЮ листовую категорию из предложенных. "
        "Отвечай строго JSON: {\"ozon_id\": int, \"ozon_type_id\": int, \"wb_id\": int, \"score\": float (0..1)}. "
        "Никакого markdown, только JSON."
    )
    ozon_short = [{"id": o["id"], "type_id": o.get("type_id"), "path": o["path"]} for o in ozon_leaves[:leaves_limit]]
    wb_short = [{"id": w["id"], "path": w["path"]} for w in wb_leaves[:leaves_limit]]
    user = (
        f"Название товара: {product_name}\n\n"
        f"Категории Ozon (id, type_id, path):\n{ozon_short}\n\n"
        f"Категории Wildberries (id, path):\n{wb_short}"
    )
    return system, user


def build_titles_prompts(
    product_name: str,
    brand: str | None,
    ozon_category_path: str,
    wb_subject_path: str,
    qty: int,
) -> tuple[str, str]:
    """Возвращает (system, user) для генерации заголовков и текстов под SKU."""
    system = (
        "Ты — копирайтер для маркетплейсов. Генерируешь заголовки и тексты по правилам:\n"
        "• Ozon title: без дефиса между брендом и товаром; для наборов префикс «Набор N шт».\n"
        "• WB title_short: без бренда. WB title_full: с брендом. Оба ≤ 60 символов.\n"
        "• WB composition ≤ 100 символов.\n"
        "• Ozon annotation: ≥ 6 предложений, под количество (qty).\n"
        "Отвечай строго JSON: {\"title_ozon\": str, \"title_wb_short\": str, \"title_wb_full\": str, "
        "\"annotation_ozon\": str, \"composition_wb\": str}. Только JSON."
    )
    user = (
        f"Товар: {product_name}\n"
        f"Бренд: {brand or '—'}\n"
        f"Категория Ozon: {ozon_category_path}\n"
        f"Категория WB: {wb_subject_path}\n"
        f"Количество в наборе (qty): {qty}\n"
    )
    return system, user
