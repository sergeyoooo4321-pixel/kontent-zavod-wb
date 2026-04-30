"""JSON-структурированные промпты для kie.ai.

Архитектура «Product Identity Layer» (без каскадной генерации):
1. Vision-LLM (gpt-5-2 vision) смотрит на ОРИГИНАЛЬНОЕ фото товара ОДИН раз
   и возвращает JSON с двумя секциями:
   - identity: что НЕЛЬЗЯ МЕНЯТЬ в товаре (форма/текст/лого/цвета упаковки)
   - design: что ДОБАВИТЬ вокруг товара (фон, палитра, плашки, типографика)
2. Image-модель генерит main/pack2/pack3/extra ПАРАЛЛЕЛЬНО:
   - input_urls=[src_url] — ВСЕГДА оригинал, НИКОГДА сгенерированные
   - в каждом промпте есть identity_lock секция (запрет менять упаковку)
   - design — общий для всей серии
   - различается только композиция (1/2/3 шт, инфографика)

Это устраняет деградацию идентичности товара.
"""
from __future__ import annotations

import json


def _json(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


# ─── design-director (vision LLM) ─────────────────────────────────


def build_design_director_system() -> str:
    """Системный промпт LLM, который ОДНОВРЕМЕННО:
    1) фиксирует идентичность товара (Product Identity Layer);
    2) сочиняет дизайн карточки.
    """
    return (
        "Ты — senior арт-директор + product analyst для карточек Ozon/Wildberries. "
        "Тебе показывают фото реального товара. Твоя двойная задача:\n"
        "  A) IDENTITY: точно зафиксировать как выглядит товар, чтобы потом "
        "     image-модель НЕ ИЗМЕНИЛА упаковку при генерации.\n"
        "  B) DESIGN: придумать продающую карточку (фон, палитра, плашки, типографика).\n\n"
        "Жёсткие правила:\n"
        "1. Внешний вид товара сохраняется ТОЧНО на всех будущих карточках: упаковка, "
        "   форма, пропорции, текст этикетки, логотипы, цвета — как на фото.\n"
        "2. Пропорция всех будущих карточек СТРОГО 3:4 вертикальная.\n"
        "3. Товар занимает не менее 50% площади.\n"
        "4. Все надписи на карточках ТОЛЬКО на русском языке, без ошибок и битых букв.\n"
        "5. Фон — *тематический под товар*, не абстракция. Примеры: порошок → "
        "   размытая прачечная; шампунь → капли воды; кофе → тёплая чашка и зёрна; "
        "   гель-лак → маникюрный салон. ТЫ САМА(А) подбираешь сцену под фото.\n"
        "6. Палитра design — современная, продающая, ГАРМОНИРУЕТ с цветами упаковки.\n"
        "7. Композиция: бренд + категория сверху, 2-3 преимущества сбоку или снизу, "
        "   плашка веса/объёма заметная.\n\n"
        "Запрещено:\n"
        "— искажать товар, менять текст на упаковке, лого, форму;\n"
        "— уменьшать товар, делать грязный или перегруженный дизайн;\n"
        "— орфографические ошибки в русском тексте.\n\n"
        "Ответ — СТРОГО JSON-объект (см. формат в user-сообщении), без markdown, без префиксов."
    )


def build_design_director_user(
    product_name: str,
    brand: str | None = None,
) -> str:
    """User-сообщение: identity + design в одном JSON."""
    return (
        "Посмотри на прикреплённое фото товара. Сделай ДВА анализа в одном JSON: "
        "(A) Product Identity — точное описание товара, который НЕЛЬЗЯ менять; "
        "(B) Design — карточка маркетплейса вокруг товара.\n\n"
        f"Имя товара: {product_name}\n"
        f"Бренд: {brand or '— возьми с упаковки если видно'}\n\n"
        "Верни СТРОГО следующий JSON:\n"
        "{\n"
        '  "identity": {\n'
        '    "shape": "string — форма упаковки (\'высокая прямоугольная бутылка с узким горлышком и красной крышкой\')",\n'
        '    "proportions": "string — пропорции (соотношение высоты к ширине, общий силуэт)",\n'
        '    "colors_packaging": ["#HEX1", "#HEX2"],  // 1-3 основных цвета упаковки\n'
        '    "label_text": "string — точный текст с этикетки (название/слоган), как на фото",\n'
        '    "brand_visual": "string — как выглядит лого/название бренда (шрифт, цвет, расположение)",\n'
        '    "key_features": ["string — характерные визуальные признаки (форма крышки, бирка, спрей-нос, окошко и т.п.)"],\n'
        '    "do_not_change": ["shape", "proportions", "label_text", "logo", "colors", "...specifics from photo"]\n'
        "  },\n"
        '  "design": {\n'
        '    "category_guess": "string — что это за товар",\n'
        '    "scene": "string — тематический фон (\'размытая прачечная с полотенцами\')",\n'
        '    "palette": ["#HEX1", "#HEX2", "#HEX3"],  // ГАРМОНИРУЕТ с colors_packaging\n'
        '    "mood": "string",\n'
        '    "brand_block": {\n'
        '      "brand_text": "string", "category_text": "string",\n'
        '      "position": "top-left|top-center|top-right", "style": "string"\n'
        "    },\n"
        '    "benefits": ["string ≤4 слов", "..."],\n'
        '    "benefits_style": "string",\n'
        '    "volume_badge": {"text": "string — объём/вес", "style": "string", "position": "bottom-right|..."},\n'
        '    "typography": "string",\n'
        '    "decorations": "string — мелкий декор по теме",\n'
        '    "product_placement": "string",\n'
        '    "overall_vibe": "string — 1-2 предложения"\n'
        "  }\n"
        "}\n\n"
        "Принципы:\n"
        "• identity описывает ТОВАР как ОН ЕСТЬ — что нельзя трогать.\n"
        "• design описывает ОКРУЖЕНИЕ — фон, плашки вокруг товара.\n"
        "• Палитра design ГАРМОНИРУЕТ с цветами упаковки (не спорит).\n"
        "• Сцена design тематически связана с категорией.\n\n"
        "Только JSON, без markdown."
    )


def compile_image_prompt(
    brief: dict,
    product_name: str,
    mode: str,  # "main" | "pack2" | "pack3" | "extra"
    qty: int = 1,
) -> str:
    """Собирает промпт для image-модели как естественный английский текст.

    На вход — JSON-бриф от vision LLM:
      {
        "identity": {shape, proportions, colors_packaging, label_text, brand_visual, key_features, ...},
        "design":   {category_guess, scene, palette, brand_block, benefits, volume_badge, ...}
      }

    На выходе — одна строка ~200 слов на английском (image-модели лучше парсят EN).
    Все user-facing надписи на карточке — на русском (через quoted text в промпте).

    Принципы (см. refactor_plan.md §3):
      • Естественный текст, не JSON. Image-модели не парсят структуру.
      • Идентичность товара через визуальное описание ("white plastic pouch with red logo"),
        а не через абстрактные do_not_change: ["shape", "proportions"].
      • Запреты формулируются позитивно — "keep packaging identical to reference".
      • Один сценарий на промпт. Никаких вложенных оверрайдов с условиями.
      • Бенефиты, плашки, бренд-блок — описаны конкретно, с цветом и расположением.
      • Палитра — в HEX в самом конце, как реминд для модели.

    brief может быть в двух форматах:
      - {"identity": {...}, "design": {...}}  — новый формат
      - {"scene": ..., "palette": ...}        — старый (для обратной совместимости)
    """
    identity = brief.get("identity") or {}
    design = brief.get("design") or (brief if "scene" in brief else {})

    # ── Блок 1: общее описание сцены ──────────────────────
    aspect = "3:4 vertical Russian marketplace product card, 2K resolution"
    scene = design.get("scene", "clean studio background with soft natural light")
    mood = design.get("mood", "clean, fresh, professional")

    # ── Блок 2: визуальное описание товара (identity) ─────
    shape = identity.get("shape", "")
    proportions = identity.get("proportions", "")
    colors = identity.get("colors_packaging", []) or []
    label_text = identity.get("label_text", "")
    brand_visual = identity.get("brand_visual", "")
    key_features = identity.get("key_features", []) or []

    product_desc_parts = ["The product is the SAME as in the reference image:"]
    if shape:
        product_desc_parts.append(f"{shape}.")
    if proportions:
        product_desc_parts.append(f"{proportions}.")
    if colors:
        product_desc_parts.append(f"Packaging colors: {', '.join(colors)}.")
    else:
        product_desc_parts.append("Packaging colors: as in reference.")
    if brand_visual:
        product_desc_parts.append(f"Label visual: {brand_visual}.")
    if label_text:
        product_desc_parts.append(f'Label text reads: "{label_text}".')
    if key_features:
        product_desc_parts.append(f"Key visual features: {', '.join(key_features)}.")
    product_desc_parts.append(
        "Keep packaging identical to reference — same shape, same colors, "
        "same logo, same label text, same proportions. Only relight, do not redesign."
    )
    product_desc = " ".join(product_desc_parts)

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
    if volume.get("text") and mode == "main":
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


def compile_bg_only_prompt(brief: dict, mode: str) -> str:
    """Промпт для генерации ТОЛЬКО фона/сцены, без товара.

    Используется в гибридном pipeline (refactor_plan.md §4), где товар вырезается
    rembg и накладывается отдельно через PIL composite. Задача image-модели здесь —
    создать чистую тематическую сцену с пустой центральной зоной для товара.
    """
    design = brief.get("design") or (brief if "scene" in brief else {})
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


# LEGACY: keep for fallback, see refactor_plan.md §3.
# Старая версия compile_image_prompt — стена JSON со 100+ полями. Image-модели парсят
# промпт линейно как обычный текст, поэтому глубокий JSON работает хуже естественного.
# Не удаляю — может понадобиться откатить если новая версия даст худший результат.
def _compile_image_prompt_legacy(
    brief: dict,
    product_name: str,
    mode: str,
    qty: int = 1,
) -> str:
    identity = brief.get("identity") or {}
    design = brief.get("design") or (brief if "scene" in brief else {})

    benefits = design.get("benefits", []) or []
    palette = design.get("palette", []) or []
    brand_block = design.get("brand_block", {}) or {}
    volume = design.get("volume_badge", {}) or {}

    identity_lock = {
        "rule": (
            "Use the EXACT SAME product from the reference image (input_urls). "
            "This is the SAME product, not a variation."
        ),
        "do_not_change": identity.get("do_not_change") or [
            "shape", "proportions", "label design", "text on label", "logo", "colors"
        ],
        "preserve": "Full visual consistency with the reference product packaging.",
        "product_features_from_reference": {
            "shape": identity.get("shape", ""),
            "proportions": identity.get("proportions", ""),
            "colors_packaging": identity.get("colors_packaging", []),
            "label_text": identity.get("label_text", ""),
            "brand_visual": identity.get("brand_visual", ""),
            "key_features": identity.get("key_features", []),
        },
        "explicit_warnings": [
            "DO NOT change shape, proportions, label design, text, logo, or colors.",
            "DO NOT generate a similar product — use THIS exact product.",
            "Improve only lighting, contrast, sharpness; keep packaging identical.",
        ],
    }

    spec = {
        "IDENTITY_LOCK": identity_lock,
        "task": "premium russian marketplace product card image",
        "aspect_ratio": "3:4 vertical",
        "resolution": "2K",
        "language": "ALL TEXT IN RUSSIAN ONLY. No English except brand if it's English.",
        "scene_background": design.get("scene", ""),
        "palette_hints_hex": palette,
        "mood": design.get("mood", ""),
        "product_must_be_unchanged": (
            "EXACT same product as in the input reference image — preserve packaging shape, "
            "label text, logos, colors, proportions. Improve lighting/contrast/sharpness only."
        ),
        "product_placement": design.get("product_placement", "centre, hero shot, slight 3/4 perspective"),
        "product_size": "товар занимает не менее 50% площади кадра",
        "brand_block": {
            "text": brand_block.get("brand_text", ""),
            "category": brand_block.get("category_text", ""),
            "position": brand_block.get("position", "top-center"),
            "style": brand_block.get("style", "modern bold sans-serif"),
        },
        "benefits": {
            "items": benefits[:3],
            "style": design.get("benefits_style", "pill bullets with small icons"),
        },
        "volume_badge": {
            "text": volume.get("text", ""),
            "style": volume.get("style", "circular bright accent badge"),
            "position": volume.get("position", "bottom-right"),
        },
        "typography": design.get("typography", "modern Russian sans-serif (Inter/Manrope/Montserrat)"),
        "decorations": design.get("decorations", "subtle, on-theme"),
        "overall_vibe": design.get("overall_vibe", ""),
        "constraints": [
            "ALL TEXT IN RUSSIAN, NO TYPOS, NO BROKEN LETTERS",
            "preserve product packaging exactly — no warping, no relabel",
            "no Lorem Ipsum, no fake brand names",
            "no clutter, professional clean composition",
            "respect 3:4 vertical safe zones",
        ],
    }

    if mode in ("pack2", "pack3"):
        spec["pack_override"] = {
            "product_units": qty,
            "caption": f"«Набор {qty} штуки»",
            "caption_position": "prominent — заменяет плашку объёма ИЛИ под брендом, крупно",
            "design_continuity": "ВЕСЬ остальной дизайн идентичен референсу: фон, палитра, шрифты, бренд, декор",
        }
        spec["task"] = (
            f"set-card showing {qty} identical units of the product, "
            "MUST match reference design language EXACTLY"
        )
    elif mode == "extra":
        spec["extra_override"] = {
            "type": "usage_instructions_card",
            "header_text": {
                "content": "«СПОСОБ ПРИМЕНЕНИЯ»",
                "style": "крупный жирный заголовок сверху, контрастный к фону",
                "position": "top centre, prominent",
            },
            "product_size": "товар занимает 60-70% площади кадра",
            "product_placement": "центр или центр-низ, hero-shot, 3/4",
            "usage_steps": {
                "count": "3-4 шага применения",
                "format": "иконка + короткая русская подпись",
                "position": "снизу или вдоль одного края",
            },
            "background_variation": "та же тематика что у main, но другой ракурс/план",
            "design_continuity": "совпадает с reference (main)",
            "language": "ВСЕ НАДПИСИ НА РУССКОМ ЯЗЫКЕ",
        }
        spec["task"] = "Карточка «Способ применения» — товар крупным планом + 3-4 шага"

    spec["product_name_for_context"] = product_name
    return _json(spec)


# ─── деприкейтнутые билдеры (оставлены для совместимости тестов) ──


def build_main_prompt(product_name: str, brand: str | None = None) -> str:
    """DEPRECATED: используется только если design_brief недоступен. Базовый универсальный промпт."""
    spec = {
        "task": "premium russian marketplace product card",
        "aspect_ratio": "3:4 vertical",
        "language": "ТОЛЬКО русский, без ошибок",
        "product": product_name,
        "brand": brand or "—",
        "design": "themed background matching product category, hero product centre, "
                  "brand top, 3 benefits as pill bullets, weight/volume badge bottom-right, "
                  "modern sans-serif, professional marketplace look",
        "constraints": ["ALL TEXT IN RUSSIAN", "no text errors", "single unit visible",
                        "no packaging distortion"],
    }
    return _json(spec)


def build_pack_prompt(product_name: str, qty: int) -> str:
    """DEPRECATED fallback."""
    spec = {
        "task": f"EXACT replica of reference design but showing {qty} units",
        "aspect_ratio": "3:4",
        "language": "ТОЛЬКО русский",
        "must_match_reference": "background, fonts, palette, brand block, decorations",
        "differences": {
            "product_count": qty,
            "caption": f"«Набор {qty} штуки»",
        },
        "product": product_name,
    }
    return _json(spec)


def build_extra_prompt(product_name: str) -> str:
    """DEPRECATED fallback."""
    spec = {
        "task": "infographic card matching reference design language",
        "aspect_ratio": "3:4",
        "language": "ТОЛЬКО русский",
        "content": "usage / composition / benefits with icons and captions",
        "must_match_reference": "palette, typography, brand block, badges",
        "product": product_name,
    }
    return _json(spec)


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


# ─── атрибуты Ozon / характеристики WB ───────────────────────────


def build_attributes_prompts(
    product_name: str,
    brand: str | None,
    category_path: str,
    qty: int,
    ozon_attrs: list[dict],
    ozon_attr_values: dict[int, list[dict]],
    *,
    examples_limit: int = 30,
) -> tuple[str, str]:
    """LLM-промпт для заполнения значений атрибутов Ozon одной SKU.

    Для атрибутов со словарём подаём топ-N значений как «examples» —
    LLM выбирает оттуда либо предлагает близкое; локально ищем по
    Левенштейну в полном словаре (см. mapping.map_ozon_attributes).
    """
    system = (
        "Ты — эксперт по заполнению карточек товара на Ozon. "
        "По названию товара, бренду, категории и списку атрибутов верни JSON. "
        "Формат: {\"<id>\": value} для одиночных атрибутов, "
        "{\"<id>\": [v1, v2, ...]} для is_collection=true. "
        "Если атрибут required — обязательно дай значение (выбирай из examples; "
        "если ничего не подходит и тип dictionary — придумай ближайшее по смыслу слово, "
        "его подберут локально). "
        "Если атрибут не required и значение неочевидно — пропусти его (не включай в JSON). "
        "Только JSON, без markdown, без префиксов."
    )

    spec: list[dict] = []
    for a in ozon_attrs:
        aid = a.get("id")
        if not aid:
            continue
        item = {
            "id": aid,
            "name": a.get("name") or "",
            "required": bool(a.get("is_required") or a.get("required")),
            "is_collection": bool(a.get("is_collection")),
        }
        dict_id = a.get("dictionary_id") or 0
        if dict_id:
            vals = ozon_attr_values.get(int(aid), [])
            item["type"] = "dictionary"
            item["examples"] = [
                v.get("value") for v in vals[:examples_limit] if v.get("value")
            ]
        else:
            item["type"] = (a.get("type") or "string").lower()
        spec.append(item)

    user = (
        f"Товар: {product_name}\n"
        f"Бренд: {brand or '—'}\n"
        f"Категория: {category_path}\n"
        f"Количество в наборе (qty): {qty}\n\n"
        f"Атрибуты:\n{json.dumps(spec, ensure_ascii=False, indent=2)}\n\n"
        "Верни строго JSON {\"<attribute_id>\": value | [values]}. "
        "Ключи — строки с числовыми id. Только JSON."
    )
    return system, user


def build_characteristics_prompts(
    product_name: str,
    brand: str | None,
    subject_path: str,
    qty: int,
    wb_charcs: list[dict],
    wb_charc_values: dict[int, list[dict]],
    *,
    examples_limit: int = 30,
) -> tuple[str, str]:
    """LLM-промпт для заполнения характеристик одной карточки WB.

    WB charcType: 0=число, 1=строка, 4=одиночный словарь, 5=мультивыбор.
    """
    system = (
        "Ты — эксперт по заполнению карточек товара на Wildberries. "
        "По названию товара, бренду, предмету (категории) и списку характеристик "
        "верни JSON. Формат: {\"<id>\": [value]} — значения ВСЕГДА массив, "
        "даже если одиночное. Числа отдавай как строки или числа — не критично. "
        "Если required — обязательно. Если не required и значение неочевидно — пропусти. "
        "Для типа dictionary выбирай из examples; если ничего не подходит — "
        "напиши ближайшее по смыслу слово, его подберут локально. "
        "Только JSON, без markdown."
    )

    spec: list[dict] = []
    for c in wb_charcs:
        cid = c.get("charcID") or c.get("id")
        if not cid:
            continue
        ctype = c.get("charcType")
        item = {
            "id": cid,
            "name": c.get("name") or "",
            "required": bool(c.get("required") or c.get("isRequired")),
            "max_count": int(c.get("maxCount") or 0),
            "type": (
                "number" if ctype == 0
                else "string" if ctype == 1
                else "dictionary_single" if ctype == 4
                else "dictionary_multi" if ctype == 5
                else "unknown"
            ),
        }
        if ctype in (4, 5):
            vals = wb_charc_values.get(int(cid), [])
            item["examples"] = [
                v.get("name") for v in vals[:examples_limit] if v.get("name")
            ]
        spec.append(item)

    user = (
        f"Товар: {product_name}\n"
        f"Бренд: {brand or '—'}\n"
        f"Предмет (категория): {subject_path}\n"
        f"Количество в наборе (qty): {qty}\n\n"
        f"Характеристики:\n{json.dumps(spec, ensure_ascii=False, indent=2)}\n\n"
        "Верни строго JSON {\"<charcID>\": [value]}. Только JSON."
    )
    return system, user
