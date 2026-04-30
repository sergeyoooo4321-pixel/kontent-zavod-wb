"""Сборщики промптов для kie.ai (image+LLM). Базовые требования из ТЗ §3.3."""
from __future__ import annotations


_BASE_STYLE = (
    "studio product photography, vertical 3:4, premium russian retail style, "
    "clean composition, no distortion of product packaging, no text errors, "
    "russian text overlays only"
)


def build_main_prompt(product_name: str, brand: str | None = None) -> str:
    brand_part = f"Бренд {brand}. " if brand else ""
    return (
        f"{_BASE_STYLE}. Product: {product_name}. Single unit. "
        f"Background — themed for product category. "
        f"{brand_part}"
        f"Russian text overlay: brand + category + 2-3 benefits + plate with weight/volume. "
        f"No text errors."
    )


def build_pack_prompt(product_name: str, qty: int) -> str:
    assert qty in (2, 3)
    return (
        f"{_BASE_STYLE}. SAME design as the reference, SAME palette and fonts. "
        f"{qty} units of {product_name}. "
        f"Russian caption: «Набор {qty} штуки»."
    )


def build_extra_prompt(product_name: str) -> str:
    return (
        f"{_BASE_STYLE}. Infographic for {product_name}: usage / composition / main benefit. "
        f"Same color logic, same fonts as reference. Russian text only."
    )


# ─── LLM prompts ──────────────────────────────────────────────────


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
