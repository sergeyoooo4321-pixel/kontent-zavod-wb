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
    2) выбирает дизайн-направление и сочиняет дизайн карточки в этом направлении.
    """
    return (
        "Ты — senior арт-директор уровня студий Свобода / Палетт / Beautyformula, "
        "делаешь продающие карточки для Ozon/Wildberries в качестве референса.\n\n"
        "Тебе показывают фото реального товара. Твоя двойная задача:\n"
        "  A) IDENTITY: точно зафиксировать как выглядит товар, чтобы image-модель "
        "     НЕ ИЗМЕНИЛА упаковку при генерации.\n"
        "  B) DESIGN: ВЫБРАТЬ ОДНО конкретное дизайн-направление и сочинить "
        "     ВЫРАЗИТЕЛЬНУЮ карточку именно в этом стиле — НЕ безликий шаблон.\n\n"
        "Дизайн-направления (выбираешь РОВНО ОДНО, исходя из визуала упаковки и категории):\n\n"
        "  • editorial_premium — журнальная вёрстка. Шрифты с засечками или ультра-тонкий sans "
        "(Playfair Display, Cormorant Garamond, Inter Light). Палитра приглушённая (бежевый, "
        "угольный, охра, кремовый). Тонкие линии-разделители. Огромный whitespace. Бенефиты — "
        "нумерованный список БЕЗ иконок. Декор минимальный, элегантный. Подходит для премиум-"
        "косметики, парфюма, чая, оливкового масла, винтажного контента.\n\n"
        "  • bold_graphic — крупные геометрические цветовые блоки. Heavy-weight sans (Druk, "
        "Helvetica Black, Manrope ExtraBold). Контрастные сочетания (красный+синий, чёрно-"
        "неоновый, жёлто-чёрный). Бенефиты — flat-tile блоки в палитре. Декор — большие фигуры "
        "(круги, прямоугольники, диагональные полосы). Подходит для бытовой химии, спортпита, "
        "энергетиков, агрессивных брендов.\n\n"
        "  • organic_botanical — природные мотивы. Кремовые/зелёные/охра/терракот. Шрифт с "
        "лёгкими засечками или рукописный курсив для слогана. Листья, ветки, цветы как "
        "паттерн фона или декоративные элементы. Бенефиты — тонкие овальные капсулы с "
        "линейными ботаническими иконками. Подходит для эко-косметики, БАДов, чая, мёда, "
        "натуральных средств.\n\n"
        "  • tech_minimal — стерильный минимал. Белый или светло-серый фон. Mono-шрифт "
        "(JetBrains Mono, IBM Plex Mono) или геометрический sans (Geist, Inter). Один "
        "акцентный цвет (электрик-синий, неоновый зелёный). Тонкая сетка. Бенефиты — короткие "
        "текстовые строки без декора, разделённые тонкой линией. Подходит для гаджетов, "
        "техники, профессиональных средств, B2B.\n\n"
        "  • luxury_dark — глубокий тёмный градиент или матовый чёрный фон. Золото, бронза, "
        "медь для текста и акцентов. Шрифт с засечками (Didot, Bodoni Moda, Playfair). Тонкие "
        "золотые линии-орнамент. Бенефиты — узкие тёмные капсулы с золотым serif-чекмарком. "
        "Подходит для парфюма, элитного алкоголя, ювелирки, премиум-косметики.\n\n"
        "  • playful_pop — яркий retro-pop, 80s-90s. Кислотные сочетания (фуксия+мята, "
        "оранж+фиолетовый). Толстые сжатые шрифты с эмоцией (Zilla Slab Display, Bungee). "
        "Стикерные плашки-вырезки с тенью, диагональные ленты, ретро-звёзды/спирали. "
        "Бенефиты — стикеры разной формы. Подходит для леденцов, газировок, молодёжного "
        "fashion, сладостей.\n\n"
        "  • vintage_apothecary — крафт-бумага, аптекарский лук конца XIX века. Шрифт с "
        "тонкими засечками (Cormorant, Old Standard). Винтажные штампы, орнаменты-рамки, "
        "медицинские иконки в стиле гравюр. Бенефиты — круглые штампы или тонкие "
        "капсулы с серифом. Подходит для аптечной продукции, БАДов, мыла ручной работы, "
        "натуральных средств с историей.\n\n"
        "  • 3d_studio — soft 3D-рендер. Glass/blob-формы вокруг продукта (полупрозрачные "
        "пузыри, abstract shapes), цветовые градиенты с лёгким blur. Современный sans "
        "(Manrope ExtraBold, Geist Bold). Бенефиты — полупрозрачные glass-капсулы с "
        "lens-blur эффектом. Подходит для ухода за кожей, гелей, кремов, современного "
        "wellness, скинкера.\n\n"
        "Жёсткие правила:\n"
        "1. Внешний вид товара сохраняется ТОЧНО (упаковка, форма, пропорции, текст этикетки, "
        "логотипы, цвета — как на фото).\n"
        "2. Пропорция карточек СТРОГО 3:4 вертикальная.\n"
        "3. Товар занимает не менее 50% площади.\n"
        "4. Все надписи на карточках ТОЛЬКО на русском, без ошибок и битых букв.\n"
        "5. Фон — тематический под товар + соответствующий выбранному design_direction.\n"
        "6. Палитра гармонирует с упаковкой И с выбранным направлением.\n"
        "7. Композиция выразительная и асимметричная, НЕ скучный шаблон по центру.\n"
        "8. Дизайн должен выглядеть как ручная работа арт-директора, не как генерик-карточка.\n\n"
        "Запрещено:\n"
        "— искажать товар, менять текст на упаковке, лого, форму;\n"
        "— делать «безликую» карточку по шаблону «бренд сверху + 3 буллета слева + объём в углу»;\n"
        "— орфографические ошибки в русском тексте;\n"
        "— смешивать направления (выбираешь ОДНО и держишь стиль).\n\n"
        "Ответ — СТРОГО JSON-объект (формат в user-сообщении), без markdown, без префиксов."
    )


def build_design_director_user(
    product_name: str,
    brand: str | None = None,
) -> str:
    """User-сообщение: identity + design (с design_direction) в одном JSON."""
    return (
        "Посмотри на прикреплённое фото товара. Сделай ДВА анализа в одном JSON: "
        "(A) Product Identity — точное описание товара, который НЕЛЬЗЯ менять; "
        "(B) Design — карточка маркетплейса в КОНКРЕТНОМ design_direction.\n\n"
        f"Имя товара: {product_name}\n"
        f"Бренд: {brand or '— возьми с упаковки если видно'}\n\n"
        "Верни СТРОГО следующий JSON:\n"
        "{\n"
        '  "identity": {\n'
        '    "shape": "форма упаковки (\'высокая прямоугольная бутылка с красной крышкой\')",\n'
        '    "proportions": "пропорции (соотношение высоты к ширине, силуэт)",\n'
        '    "colors_packaging": ["#HEX1", "#HEX2"],\n'
        '    "label_text": "точный текст с этикетки (название/слоган)",\n'
        '    "brand_visual": "как выглядит лого (шрифт, цвет, расположение)",\n'
        '    "key_features": ["характерные признаки (форма крышки, бирка, спрей-нос, окошко)"],\n'
        '    "do_not_change": ["shape", "proportions", "label_text", "logo", "colors"]\n'
        "  },\n"
        '  "design": {\n'
        '    "category_guess": "что это за товар",\n'
        '    "design_direction": "одно из: editorial_premium | bold_graphic | organic_botanical | tech_minimal | luxury_dark | playful_pop | vintage_apothecary | 3d_studio",\n'
        '    "direction_reason": "1 предложение почему этот стиль подходит товару",\n'
        '    "scene": "тематический фон С УЧЁТОМ ВЫБРАННОГО НАПРАВЛЕНИЯ (не просто \'студия\', а сцена-в-стиле, например для luxury_dark — \'тёмный мраморный стол с золотым лучом света\')",\n'
        '    "palette": ["#HEX1", "#HEX2", "#HEX3"],\n'
        '    "accent_color": "#HEX — главный акцент, контрастирует с упаковкой",\n'
        '    "mood": "1-2 слова",\n'
        '    "brand_block": {\n'
        '      "brand_text": "string",\n'
        '      "category_text": "string (короткое описание категории)",\n'
        '      "position": "top-left|top-center|top-right|bottom-left",\n'
        '      "style_notes": "конкретика именно для этого направления (\'тонкая засечка на охре\', \'жирный sans на красном круге\', \'золотой Didot на чёрном\')"\n'
        "    },\n"
        '    "benefits": ["короткая фраза ≤4 слов", "...", "..."],\n'
        '    "benefits_render_hint": "как именно нарисовать бенефиты под выбранное направление (\'нумерованный список тонкими цифрами\', \'flat tiles в палитре\', \'glass-капсулы с soft-shadow\', \'круглые винтажные штампы\')",\n'
        '    "volume_badge": {"text": "объём/вес", "style_notes": "под направление", "position": "bottom-right|..."},\n'
        '    "typography_hint": "конкретные шрифты-примеры (\'Playfair Display + Inter\', \'Druk Wide + Manrope\', \'Cormorant + Old Standard\')",\n'
        '    "decorations": "конкретный декор под направление (\'тонкие охряные линии-разделители\', \'диагональные неоновые полосы\', \'ботанические листья по углам\', \'винтажная рамка-орнамент\')",\n'
        '    "product_placement": "где и как лежит/стоит товар (\'центр-низ слева от золотого луча\', \'диагональ снизу-вверх с тенью\')",\n'
        '    "composition_notes": "выразительная асимметрия, не центр в лоб (\'продукт в правой трети, текст слева\', \'диагональная разрезка кадра\')",\n'
        '    "overall_vibe": "1-2 предложения как карточка должна выглядеть в целом"\n'
        "  }\n"
        "}\n\n"
        "Принципы:\n"
        "• design_direction выбирай ОСМЫСЛЕННО под товар: химия → bold_graphic, аптека → "
        "vintage_apothecary, премиум-косметика → editorial_premium / luxury_dark, "
        "уход за кожей → 3d_studio, эко → organic_botanical, гаджет → tech_minimal, "
        "сладости/детское → playful_pop. Если сомневаешься — выбирай НЕ tech_minimal "
        "(он самый скучный).\n"
        "• palette + accent_color должны соответствовать выбранному направлению "
        "(luxury_dark = тёмные + золото, не пастель; playful_pop = кислотные, не нюд).\n"
        "• ВСЕ style_notes / render_hint / decorations пиши КОНКРЕТНО, не \"красиво\".\n"
        "• Композиция должна быть выразительной — асимметрия, диагональ, large hero-text.\n"
        "• Идеал — карточка как из дизайн-портфолио, не маркетплейс-генерик.\n\n"
        "Только JSON, без markdown."
    )


# ── Дизайн-направления (см. build_design_director_system) ────────
# Каждое направление задаёт КОНКРЕТНЫЕ render-хинты для image-модели:
# фоновую сцену-аугментацию, типографику, стиль бенефитов/брендблока/объёма/декора.
# vision-LLM выбирает direction в brief["design"]["design_direction"], но если
# не выбрал — fallback на editorial_premium (наиболее универсальный premium-вид).

_DIRECTION_RENDERERS: dict[str, dict[str, str]] = {
    "editorial_premium": {
        "stage_hint": "magazine-editorial premium look, lots of whitespace, asymmetric layout, hand-crafted by an art director",
        "typography": "elegant serif headlines (Playfair Display / Cormorant Garamond) combined with thin Inter Light or Cormorant Italic for body",
        "brand_block": "huge serif brand wordmark in muted dark color (#2A1F1A or charcoal), set as a hero headline in the upper-left corner, NOT inside a pill — like a magazine cover; below it a thin horizontal hairline divider in metallic ochre",
        "benefits": "numbered list (01., 02., 03.) on the left side, thin uppercase letterspaced caption-style Russian text in muted color, NO icons, NO pills, NO checkmarks — pure typography with hairline dividers between items",
        "volume": "small thin uppercase letterspaced caption like '750 ML / 25.4 OZ' in the bottom corner, set as a single-line text label, NOT a circular badge",
        "decor": "two ultra-thin horizontal hairlines in metallic ochre (#B8956A) crossing the composition, very minimal, restrained",
        "palette_default": "muted editorial palette — warm beige, charcoal, ochre, cream",
    },
    "bold_graphic": {
        "stage_hint": "bold graphic poster, flat geometric color blocks, high-contrast, strong asymmetric composition",
        "typography": "ultra heavy condensed sans-serif (Druk Wide, Helvetica Black, Manrope ExtraBold)",
        "brand_block": "GIANT sans-serif brand wordmark filling the top third of the card, set on a flat solid color block (use accent_color from palette); the wordmark is in white or paper color",
        "benefits": "three flat tile blocks stacked vertically on the left, each tile is a solid rectangle in a different palette color, with the benefit text in heavy white sans-serif, NO icons, NO pills — pure flat geometric blocks",
        "volume": "huge rotated solid color circle in the bottom-right with the volume in heavy white sans-serif inside",
        "decor": "diagonal stripes or large geometric shapes (circles, triangles) bleeding off the edges in palette colors",
        "palette_default": "high-contrast bold palette — primary red, deep blue, mustard yellow, off-white",
    },
    "organic_botanical": {
        "stage_hint": "natural organic look, botanical illustration style, soft daylight, slight grain texture",
        "typography": "humanist serif with light italic accents (Cormorant Italic for tagline, Inter Light for body)",
        "brand_block": "elegant serif brand name in deep forest-green or warm terracotta, placed top-center with a small hand-drawn botanical leaf flourish underneath",
        "benefits": "three slim oval capsules stacked or arranged in a soft arc, each capsule is cream/off-white with a thin botanical line-icon (leaf, drop, flower) on the left and the Russian benefit text in serif italic on the right",
        "volume": "small cream-colored circular wax-seal-style badge in the bottom-right with serif text inside",
        "decor": "translucent botanical illustrations (eucalyptus leaves, herbs, dried flowers) in olive/terracotta tones drifting in from the corners",
        "palette_default": "warm botanical palette — sage green, terracotta, cream, olive",
    },
    "tech_minimal": {
        "stage_hint": "stark minimal tech-product look, clean white or light-grey background, thin grid lines hint",
        "typography": "geometric sans (Geist, Inter) headlines + monospace accents (JetBrains Mono / IBM Plex Mono) for technical labels",
        "brand_block": "compact clean sans brand wordmark in solid black, placed top-left with a small monospace category tag in a single accent color underneath",
        "benefits": "three short text-only rows on the left, each row starts with a tiny accent-colored bullet (a square dot), the benefit text in clean black sans-serif, separated by ultra-thin grey hairlines, NO icons, NO capsules",
        "volume": "small monospace text label in the bottom-right corner like '750 ML' set in JetBrains Mono, NO badge, NO circle",
        "decor": "subtle thin grid lines barely visible in the background, one bold accent-color line as a structural divider",
        "palette_default": "tech minimal palette — pure white, deep black, single neon accent (electric blue or acid green)",
    },
    "luxury_dark": {
        "stage_hint": "luxury dark editorial — deep matte black or dark gradient background, single dramatic gold light beam falling on the product",
        "typography": "high-contrast Didone serif (Bodoni Moda, Didot, Playfair) + thin Inter for body",
        "brand_block": "thin elegant serif brand wordmark in metallic gold (#C9A45C) placed top-center, with a fine gold horizontal hairline above and below",
        "benefits": "three slim dark capsules with subtle gold inner border, each contains a tiny gold serif checkmark and the Russian benefit text in light serif italic",
        "volume": "small ornamental gold ring badge in the bottom-right with serif gold text inside",
        "decor": "ultra-thin gold ornamental flourishes (corner brackets, hairline frames) in the corners, very restrained, premium",
        "palette_default": "luxury dark palette — matte black, deep charcoal, metallic gold, warm cream highlights",
    },
    "playful_pop": {
        "stage_hint": "vibrant 80s-90s retro-pop poster, sticker-cutout aesthetic, slight halftone texture, fun and energetic",
        "typography": "chunky display fonts (Bungee, Zilla Slab Display, Druk Display) — bold, condensed, full of personality",
        "brand_block": "wavy or arched brand wordmark in a bright pop color sitting on a contrasting solid-color circle or wavy banner shape",
        "benefits": "three sticker-cutout shapes (a star, a wavy circle, a rounded rectangle) in different vivid palette colors with hard drop-shadows, the Russian benefit text in chunky display font",
        "volume": "rotated sticker-burst shape (jagged star or wavy circle) in a contrasting color in the bottom-right with the volume in chunky display font",
        "decor": "diagonal candy-stripe ribbons, tiny retro stars, halftone dot patterns, swirly accent lines in vivid palette colors",
        "palette_default": "vibrant pop palette — hot pink, mint green, electric purple, citrus yellow",
    },
    "vintage_apothecary": {
        "stage_hint": "late-19th-century apothecary look, cream kraft-paper texture background, gentle vignette, vintage engraved feel",
        "typography": "classic serifs (Cormorant Garamond, Old Standard TT) with slight letterspacing — formal, archival",
        "brand_block": "ornate serif brand wordmark inside a thin double-rule rectangular frame, positioned top-center, with a small engraving-style ornament underneath",
        "benefits": "three round vintage stamp-style badges (deep ink-red or sepia) with thin serif Russian text inside and a small engraved illustration (mortar, leaf, flask)",
        "volume": "round wax-seal-style badge in the bottom-right with engraving-style serif text",
        "decor": "thin vintage-engraved line ornaments in the corners (curled flourishes, botanical engravings, apothecary symbols), all in deep ink color",
        "palette_default": "apothecary palette — kraft cream, deep sepia, ink black, faded burgundy",
    },
    "3d_studio": {
        "stage_hint": "soft 3D studio render, translucent glass blob shapes and abstract gradient orbs floating around the product, lens-blur depth",
        "typography": "modern geometric sans (Manrope ExtraBold, Geist Bold) with smooth letterforms",
        "brand_block": "clean modern sans brand wordmark with a soft drop-shadow on a translucent glass capsule (frosted-glass look) placed top-center",
        "benefits": "three frosted-glass translucent capsules with subtle inner glow, each contains a small soft-gradient icon dot and the Russian benefit text in clean sans, lens-blur background visible through them",
        "volume": "soft-gradient sphere with a tiny lens-flare in the bottom-right, the volume text in clean sans on top",
        "decor": "translucent 3D blob shapes (mint-glass, peach-glass, lavender-glass) drifting around the product with soft shadows and lens-blur",
        "palette_default": "soft 3D palette — pastel mint, peach, lavender, off-white",
    },
}

_DIRECTION_FALLBACK = "editorial_premium"


def _renderers_for(direction: str) -> dict[str, str]:
    return _DIRECTION_RENDERERS.get(direction) or _DIRECTION_RENDERERS[_DIRECTION_FALLBACK]


def compile_image_prompt(
    brief: dict,
    product_name: str,
    mode: str,  # "main" | "pack2" | "pack3" | "extra"
    qty: int = 1,
) -> str:
    """Собирает промпт для image-модели как естественный английский текст.

    На вход — JSON-бриф от vision LLM с design_direction (один из 8 стилей).
    На выходе — одна строка ~250-300 слов на английском, рендерящая выбранный стиль.

    Все user-facing надписи на карточке — на русском (через quoted text в промпте).
    Image-модели лучше парсят естественный текст, чем JSON.
    """
    identity = brief.get("identity") or {}
    design = brief.get("design") or (brief if "scene" in brief else {})

    # ── 0. Дизайн-направление ─────────────────────────────
    direction = (design.get("design_direction") or _DIRECTION_FALLBACK).strip()
    rd = _renderers_for(direction)

    # ── 1. Сцена + общий стейдж ───────────────────────────
    aspect = "3:4 vertical Russian marketplace product card, 2K resolution"
    scene = design.get("scene", "soft natural light, neutral background")
    mood = design.get("mood", "premium, intentional, art-directed")
    stage_hint = rd["stage_hint"]
    composition_notes = design.get("composition_notes",
        "asymmetric, off-center hero, intentional whitespace, NOT a centered template")

    # ── 2. Товар (identity) ───────────────────────────────
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

    # ── 3. Композиция (зависит от mode) ───────────────────
    if mode == "main":
        composition = (
            "Composition: single hero product, 50–60% of frame, asymmetric placement "
            "(NOT centered in the geometric center), slight 3/4 angle, subtle natural shadow."
        )
        units_caption = ""
    elif mode == "pack2":
        composition = (
            "Composition: TWO IDENTICAL product packages side by side, both 100% identical "
            "to each other and to the reference. Same lighting, same shadow direction. "
            "Asymmetric placement in the frame (slightly off-center), moderate spacing, no overlap."
        )
        units_caption = (
            'Prominent caption "Набор 2 штуки" rendered in the chosen design direction\'s '
            'typography, placed where it strengthens composition (NOT generic top-center).'
        )
    elif mode == "pack3":
        composition = (
            "Composition: THREE IDENTICAL product packages arranged in a fan/row, all "
            "100% identical to each other and to the reference. Same lighting and shadow direction. "
            "Slight overlap or staggered spacing for editorial rhythm."
        )
        units_caption = (
            'Prominent caption "Набор 3 штуки" rendered in the chosen design direction\'s '
            'typography, placed where it strengthens composition.'
        )
    elif mode == "extra":
        composition = (
            "Composition: single hero product, 60–70% of frame, dynamic angle. Plus 3-4 numbered "
            "usage step icons (rendered in the chosen design direction's icon style) along the "
            "bottom edge with short Russian captions describing how to use the product."
        )
        units_caption = (
            'Top header «СПОСОБ ПРИМЕНЕНИЯ» rendered in the chosen design direction\'s headline '
            'typography, placed prominently at the top.'
        )
    else:
        composition = "Composition: asymmetric, intentional product placement."
        units_caption = ""

    # ── 4. Стилевые блоки (под выбранное направление) ─────
    brand_block = design.get("brand_block", {}) or {}
    benefits = design.get("benefits", []) or []
    volume = design.get("volume_badge", {}) or {}
    palette = design.get("palette", []) or []
    accent = design.get("accent_color", "")
    typography_hint = design.get("typography_hint") or rd["typography"]
    decorations = design.get("decorations") or rd["decor"]
    benefits_render_hint = design.get("benefits_render_hint") or rd["benefits"]

    brand_text = brand_block.get("brand_text", "")
    category_text = brand_block.get("category_text", "")
    brand_style_notes = brand_block.get("style_notes") or rd["brand_block"]

    brand_text_block = ""
    if brand_text:
        brand_text_block = (
            f'Brand block: render "{brand_text}"'
            + (f' / "{category_text}"' if category_text else '')
            + f". {brand_style_notes}."
        )

    benefits_text = ""
    if benefits:
        items = " · ".join(f'"{b}"' for b in benefits[:3])
        benefits_text = (
            f"Benefits ({len(benefits[:3])} items): {items}. "
            f"Render style: {benefits_render_hint}."
        )

    volume_text = ""
    if volume.get("text") and mode == "main":
        volume_style = volume.get("style_notes") or rd["volume"]
        volume_text = f'Volume label "{volume["text"]}". Render style: {volume_style}.'

    palette_hex = ", ".join(palette) if palette else rd["palette_default"]
    accent_hint = f" Accent color: {accent}." if accent else ""

    # ── 5. Финальная сборка ───────────────────────────────
    parts = [
        f"{aspect}.",
        f"DESIGN DIRECTION: {direction}. {stage_hint}.",
        f"Scene: {scene}. Mood: {mood}.",
        f"Composition principle: {composition_notes}.",
        product_desc,
        composition,
        units_caption,
        brand_text_block,
        benefits_text,
        volume_text,
        f"Typography: {typography_hint}.",
        f"Decorative elements: {decorations}.",
        f"Palette: {palette_hex}.{accent_hint}",
        "All on-card Russian text must be perfectly legible, no typos, no broken letters. "
        "The whole card must look like an art-director's portfolio piece in the chosen "
        "design direction — NOT a generic marketplace template. "
        "Respect 3:4 vertical safe zones — important text away from edges.",
    ]

    prompt = " ".join(p.strip() for p in parts if p.strip())
    return prompt

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
    """Возвращает (system, user) для генерации заголовков и текстов под SKU.

    Правила соответствуют регламенту «API-добавление Ozon/Wildberries» §11.2,
    §11.5, §12.2-12.3, §12.6 + чек-листы §18.2-18.3.
    """
    system = (
        "Ты — копирайтер для маркетплейсов. Строго следуй правилам:\n\n"
        "ИМЕНОВАНИЕ\n"
        "• Ozon title_ozon: формат «[Набор N шт ]Бренд product_part». "
        "БЕЗ дефиса между брендом и товаром (даже если в исходной строке стоит «Brand - Product»). "
        "Для qty=2 префикс «Набор 2 шт», для qty=3 — «Набор 3 шт» В НАЧАЛЕ, без точки/двоеточия.\n"
        "• WB title_wb_short: БЕЗ бренда, ≤60 символов, без обрыва на середине слова. "
        "Если имя длиннее — аккуратно сократи, сохранив тип, вкус/вариант, объём.\n"
        "• WB title_wb_full: С брендом, может быть длиннее 60. Для наборов префикс «Набор N шт».\n\n"
        "АННОТАЦИЯ И СОСТАВ\n"
        "• annotation_ozon: МИНИМУМ 6 ПОЛНОЦЕННЫХ ПРЕДЛОЖЕНИЙ. Уникальная под каждый qty: "
        "для qty=2/3 явно упомяни «комплект из 2/3 единиц», объясни преимущество запаса/семейного формата. "
        "Описание товарное: назначение, ключевые свойства, аромат, упаковка, кому подходит, способ применения. "
        "Не пиши неподтверждённых лечебных свойств.\n"
        "• composition_wb: ≤100 символов. Сокращай ключевыми компонентами, не обрывая текст.\n\n"
        "ОБЩЕЕ\n"
        "• Дефис в исходной строке нужен ТОЛЬКО для разделения бренда и товара — в Ozon-имени его не оставляй.\n"
        "• Не самовольно перепридумывай товар. Сохраняй вкус/аромат/объём, как в исходной строке.\n\n"
        "Ответ — СТРОГО JSON: {\"title_ozon\": str, \"title_wb_short\": str, \"title_wb_full\": str, "
        "\"annotation_ozon\": str, \"composition_wb\": str}. Только JSON, без markdown."
    )
    user = (
        f"Товар (исходная строка пользователя): {product_name}\n"
        f"Бренд: {brand or '—'}\n"
        f"Категория Ozon: {ozon_category_path}\n"
        f"Категория WB: {wb_subject_path}\n"
        f"Количество в наборе (qty): {qty}\n\n"
        f"Ожидаемые форматы:\n"
        f"• title_ozon: «"
        + (f"Набор {qty} шт " if qty > 1 else "")
        + "Бренд product_part» — БЕЗ дефиса между брендом и товаром.\n"
        f"• title_wb_short: краткое БЕЗ бренда, ≤60.\n"
        f"• title_wb_full: с брендом"
        + (f", префикс «Набор {qty} шт»" if qty > 1 else "")
        + ".\n"
        f"• annotation_ozon: ≥6 предложений, для qty>1 явно «комплект из {qty} штук».\n"
        f"• composition_wb: ≤100 символов."
    )
    return system, user


def build_titles_prompts_batch(
    product_name: str,
    brand: str | None,
    ozon_category_path: str,
    wb_subject_path: str,
    qtys: list[int],
) -> tuple[str, str]:
    """То же что build_titles_prompts, но за ОДИН LLM-вызов на ВСЕ qty.

    Возвращает (system, user) для запроса. LLM вернёт JSON
    {"<qty>": {title_ozon, title_wb_short, title_wb_full, annotation_ozon, composition_wb}}.
    """
    system = (
        "Ты — копирайтер для маркетплейсов. Тебе дают ОДИН товар и список "
        "вариантов qty (1, 2, 3 — одиночка, набор 2, набор 3). Ты возвращаешь "
        "тексты для КАЖДОГО варианта одним JSON.\n\n"
        "ИМЕНОВАНИЕ\n"
        "• Ozon title_ozon: «[Набор N шт ]Бренд product_part». БЕЗ дефиса между брендом и товаром.\n"
        "• WB title_wb_short: БЕЗ бренда, ≤60 символов, без обрыва на середине слова.\n"
        "• WB title_wb_full: С брендом, для qty>1 префикс «Набор N шт».\n\n"
        "АННОТАЦИЯ И СОСТАВ\n"
        "• annotation_ozon: ≥6 ПОЛНОЦЕННЫХ предложений, УНИКАЛЬНАЯ под каждый qty. "
        "Для qty=2/3 явно упомяни «комплект из N единиц», объясни преимущество запаса.\n"
        "• composition_wb: ≤100 символов. Сокращай ключевыми компонентами.\n\n"
        "ФОРМАТ ОТВЕТА\n"
        "Строго JSON: {\"1\": {...}, \"2\": {...}, \"3\": {...}}\n"
        "Каждое значение — объект с полями title_ozon, title_wb_short, title_wb_full, "
        "annotation_ozon, composition_wb. Только JSON, без markdown."
    )
    qtys_str = ", ".join(str(q) for q in qtys)
    user = (
        f"Товар (исходная строка): {product_name}\n"
        f"Бренд: {brand or '—'}\n"
        f"Категория Ozon: {ozon_category_path}\n"
        f"Категория WB: {wb_subject_path}\n"
        f"qty варианты: {qtys_str}\n\n"
        f"Верни JSON {{\"1\": {{...}}, ...}} для каждого qty из списка."
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
        "Следуй регламенту «API-добавление Ozon/Wildberries» §11 строго.\n\n"
        "ПРИОРИТЕТ ИСТОЧНИКОВ (§5):\n"
        "1) Текст на упаковке товара. 2) Официальный сайт бренда. "
        "3) Наиболее вероятное значение по контексту (только если 1-2 нет).\n"
        "Не придумывай экзотические свойства, не заполняй бессмысленные поля.\n\n"
        "ПРАВИЛА:\n"
        "• Required атрибуты — заполняй ОБЯЗАТЕЛЬНО.\n"
        "• Если есть examples (тип dictionary) — выбирай из них точное значение в той же словоформе.\n"
        "  Если ничего не подходит — напиши ближайшее по смыслу слово, локальный матчинг подберёт.\n"
        "• Если атрибут не required и значения нет — пропусти (не включай в JSON).\n"
        "• Если атрибут — «Группа» / «Группа товаров» — формат «Бренд - категория» (§11.3).\n"
        "• Если атрибут — «Вес товара, г» — это ОДИНОЧНЫЙ вес товара (одинаков для qty=1/2/3).\n"
        "• Если атрибут — «Вес в упаковке, г» / «Логистический вес» — это вес ВСЕГО набора, увеличивается для qty>1.\n"
        "• Целевую аудиторию, действие, особенности применения, тип кожи/волос — выбирай ТОЛЬКО из examples.\n"
        "• Множественные значения для is_collection=true — массив [v1, v2, ...]. НЕ склеенная строка с «;».\n\n"
        "ФОРМАТ ОТВЕТА:\n"
        "• Одиночные атрибуты: {\"<id>\": value}\n"
        "• Коллекции: {\"<id>\": [v1, v2, ...]}\n"
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
        "Следуй регламенту «API-добавление Ozon/Wildberries» §12 строго.\n\n"
        "ПРИОРИТЕТ ИСТОЧНИКОВ (§5):\n"
        "1) Текст на упаковке. 2) Официальный сайт бренда. 3) Вероятное значение по контексту.\n\n"
        "ПРАВИЛА WB:\n"
        "• Required — заполняй ОБЯЗАТЕЛЬНО.\n"
        "• Для типа dictionary (charcType=4) или dictionary_multi (charcType=5) — выбирай из examples "
        "в той же словоформе.\n"
        "• Вкус/аромат — заполняй ОБЯЗАТЕЛЬНО, если он есть в названии или на упаковке (§12.6).\n"
        "• Упаковку (тюбик/флакон/бутылка/коробка/пакет) — заполняй, если можно определить.\n"
        "• ТНВЭД-код — обязателен; если не уверен — выбирай из examples ближайший по типу товара.\n"
        "• Состав не должен быть длиннее 100 символов — сокращай ключевыми компонентами без обрыва.\n"
        "• Целочисленные габариты, округление вверх; меньшая сторона × qty для наборов (§12.7).\n"
        "• Числовые значения — числа или строки чисел; для charcType=0 (число) лучше число.\n\n"
        "ФОРМАТ:\n"
        "{\"<id>\": [value]} — значения ВСЕГДА массив, даже одиночные.\n"
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
