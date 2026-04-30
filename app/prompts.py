"""JSON-структурированные промпты для kie.ai.

Двухступенчатая логика:
1. Vision-LLM смотрит на фото товара и СОЧИНЯЕТ JSON-бриф дизайна (фон, палитра,
   текстовые блоки, шрифты, декор) — индивидуально под этот товар.
2. Image-модель использует этот бриф как промпт для генерации.

Pack2/pack3/extra используют тот же бриф (с правкой qty/контента) + main как ref.
"""
from __future__ import annotations

import json


def _json(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


# ─── design-director (vision LLM) ─────────────────────────────────


def build_design_director_system() -> str:
    """Системный промпт LLM-арт-директора. Адаптировано из user-driven brief."""
    return (
        "Ты — арт-директор продающих карточек товаров для Ozon и Wildberries. "
        "Тебе показывают фото реального товара. Твоя задача — придумать "
        "*индивидуальный* дизайн карточки, который продаёт ИМЕННО ЭТОТ товар.\n\n"
        "Жёсткие правила (нельзя нарушать):\n"
        "1. Внешний вид товара сохраняется ТОЧНО: упаковка, форма, пропорции, текст, "
        "   логотипы, расположение элементов, цвет — всё как на фото.\n"
        "2. Пропорция изображения СТРОГО 3:4 вертикальная.\n"
        "3. Товар занимает не менее 50% площади.\n"
        "4. Все надписи ТОЛЬКО на русском языке, без ошибок и битых букв.\n"
        "5. Фон — *тематический под товар*, не абстракция. Например: для порошка — "
        "   стиральная машина в размытом интерьере прачечной; для шампуня — капли "
        "   воды и струйка волос; для кофе — тёплая чашка и зёрна на дереве; "
        "   для гель-лака — наманикюренная рука и маникюрный салон. ТЫ САМА(А) "
        "   подбираешь сцену под фото.\n"
        "6. Палитра — современная, продающая, контрастная; без грязи и перегруза.\n"
        "7. Композиция: бренд + категория сверху, 2-3 преимущества сбоку или снизу, "
        "   плашка веса/объёма заметная.\n"
        "8. Современные иконки/пиктограммы допустимы (но без перегруза).\n\n"
        "Запрещено:\n"
        "— искажать товар, менять текст на упаковке, добавлять несуществующие элементы;\n"
        "— уменьшать товар, делать грязный или перегруженный дизайн;\n"
        "— орфографические ошибки в русском тексте;\n"
        "— нарушать формат 3:4.\n\n"
        "Тон — профессиональный дизайнер, лаконично.\n\n"
        "Ответ — СТРОГО JSON-объект (см. формат в user-сообщении). Без markdown, без префиксов."
    )


def build_design_director_user(
    product_name: str,
    brand: str | None = None,
) -> str:
    """User-сообщение для LLM-арт-директора. Описывает что должно быть в JSON-ответе."""
    return (
        "Посмотри на прикреплённое фото товара и сочини дизайн-бриф для карточки маркетплейса.\n\n"
        f"Имя товара: {product_name}\n"
        f"Бренд: {brand or '— не указан, придумай или возьми с упаковки'}\n\n"
        "Верни СТРОГО следующий JSON:\n"
        "{\n"
        '  "category_guess": "string — что это за товар (например, шампунь, порошок, кофе)",\n'
        '  "scene": "string — описание тематического фона (что в кадре кроме товара). '
        'Например: \'размытая прачечная с белыми полотенцами и солнечным светом из окна\'",\n'
        '  "palette": ["#HEX1", "#HEX2", "#HEX3"],  // 3 цвета: основной/акцент/нейтральный\n'
        '  "mood": "string — настроение (например: чисто и свежо / уютно и тепло / премиально)",\n'
        '  "brand_block": {\n'
        '    "brand_text": "string — точное название бренда из упаковки",\n'
        '    "category_text": "string — категория, 1-2 слова на русском",\n'
        '    "position": "top-left | top-center | top-right",\n'
        '    "style": "string — описание стиля бренд-блока"\n'
        "  },\n"
        '  "benefits": [\n'
        '    "string — 1-е преимущество, короткое (до 4 слов)",\n'
        '    "string — 2-е преимущество",\n'
        '    "string — 3-е преимущество (опционально)"\n'
        "  ],\n"
        '  "benefits_style": "string — как они оформлены (pill bullets / иконки + текст / etc)",\n'
        '  "volume_badge": {\n'
        '    "text": "string — вес/объём/количество, если видно на упаковке (например \'250 мл\')",\n'
        '    "style": "string — оформление (круглая яркая плашка / прямоугольный бейдж / etc)",\n'
        '    "position": "bottom-right | bottom-left | top-right"\n'
        "  },\n"
        '  "typography": "string — описание шрифтов (например: \'крупный жирный sans-serif для бренда, средний для преимуществ\')",\n'
        '  "decorations": "string — мелкие декор-элементы (брызги воды, листья, пар, искорки и т.д.) — что подходит к товару",\n'
        '  "product_placement": "string — где и как стоит товар в кадре (центр-право, лёгкий поворот, hero-shot и т.д.)",\n'
        '  "overall_vibe": "string — 1-2 предложения, как должна ВЫГЛЯДЕТЬ карточка целиком"\n'
        "}\n\n"
        "Принципы выбора:\n"
        "• Фон должен ассоциироваться с товаром, не быть абстрактным цветным градиентом.\n"
        "• Палитра — гармонирует с упаковкой товара, не спорит с ней.\n"
        "• Преимущества — реальные характеристики, видимые с упаковки или очевидные из категории.\n"
        "• Bonus: если на упаковке есть объём/вес/количество — переноси точно.\n\n"
        "Ничего лишнего. Только JSON."
    )


def compile_image_prompt(
    design: dict,
    product_name: str,
    mode: str,  # "main" | "pack2" | "pack3" | "extra"
    qty: int = 1,
) -> str:
    """Превращает JSON-бриф от LLM-арт-директора в финальный текстовый промпт для image-модели.

    Включает все требования из ТЗ + брифа, без интерпретации (дизайн уже задан LLM).
    """
    benefits = design.get("benefits", []) or []
    palette = design.get("palette", []) or []
    brand_block = design.get("brand_block", {}) or {}
    volume = design.get("volume_badge", {}) or {}

    # Build a compact spec
    spec = {
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

    if mode == "pack2" or mode == "pack3":
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
                "style": (
                    "крупный жирный заголовок сверху, красивый продающий русский шрифт, "
                    "контрастный к фону, ВСЕ ЗАГЛАВНЫМИ или капс-стиль"
                ),
                "position": "top centre, prominent",
            },
            "product_size": (
                "товар КРУПНЕЕ чем на main — занимает 60-70% площади кадра, "
                "хорошо видны детали упаковки и текст этикетки"
            ),
            "product_placement": "центр или центр-низ, hero-shot, чуть наклонён или 3/4",
            "usage_steps": {
                "count": "3-4 шага применения, последовательно",
                "format": "иконка + короткая русская подпись (1-3 слова)",
                "examples_by_category": {
                    "чистящее средство": ["1. Нанести", "2. Распределить", "3. Подождать", "4. Смыть"],
                    "шампунь": ["1. Нанести на влажные волосы", "2. Вспенить", "3. Смыть водой"],
                    "крем": ["1. Очистить кожу", "2. Нанести", "3. Втирать массажными движениями"],
                    "кофе": ["1. Залить горячей водой", "2. Подождать 4 мин", "3. Наслаждаться"],
                    "default": "придумай шаги применения логично под этот товар",
                },
                "style": "минималистичные иконки в стиле линейная графика или плоские, цвета из палитры",
                "position": "снизу или вдоль одного края — НЕ перекрывает товар",
            },
            "background_variation": (
                "ТА ЖЕ тематика что у main (та же сцена/окружение), но КОМПОЗИЦИЯ слегка изменена — "
                "другой ракурс, другой план, другое время суток, другие декор-элементы из той же темы. "
                "НЕ дубликат main. Примеры: для main «кухонная раковина общим планом» — extra «крупный план "
                "на той же раковине с каплями воды»; для main «прачечная общим планом» — extra «полки с бельём в той же прачечной»."
            ),
            "design_continuity": (
                "ОБЯЗАТЕЛЬНО совпадает с reference (main): палитра, шрифты, бренд-блок, стиль декора, общая стилистика"
            ),
            "no_brand_block_needed": False,
            "language": "ВСЕ НАДПИСИ НА РУССКОМ ЯЗЫКЕ, без ошибок",
        }
        spec["task"] = (
            "Карточка «Способ применения» — тот же товар крупным планом, "
            "та же тематическая сцена что на main (но в чуть изменённом ракурсе/композиции), "
            "крупный красивый заголовок «СПОСОБ ПРИМЕНЕНИЯ», 3-4 шага с иконками. "
            "MUST match reference design language."
        )

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
