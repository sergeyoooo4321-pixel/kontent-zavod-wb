"""Нормализация исходной строки товара и форматирование названий по регламенту.

Реализует разделы 7, 11.2, 12.2-12.3 регламента «API-добавление Ozon/Wildberries»:
  • parse_input_line(name) — разбор «Бренд - Тип Вариант Объём» на компоненты
  • format_ozon_title — без дефиса между брендом и товаром, префикс «Набор N шт»
  • format_wb_short_title — без бренда, ≤60 символов, аккуратный trim по словам
  • format_wb_full_title — с брендом
"""
from __future__ import annotations

import re

# Объём/вес — двух- и однобуквенные единицы плюс «штук»
_VOLUME_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(мл|л|г|гр|кг|ml|l|kg|gr)\b\.?",
    re.IGNORECASE,
)
# Возрастная маркировка: «0+», «1+» (приоритет), затем «детский», «детская», «для детей»
_AGE_PLUS_RE = re.compile(r"\b(\d+\s*\+)")
_AGE_WORD_RE = re.compile(
    r"\b(детск\w*|малыш\w*|младенч\w*|подростк\w*|для\s+детей)\b",
    re.IGNORECASE,
)


def parse_input_line(name: str, brand_hint: str | None = None) -> dict[str, str]:
    """Разбор названия товара на компоненты.

    Вход: «Tide - Стиральный порошок Альпийская свежесть 400 г».
    Выход: {brand, product_part, volume, age_target, name_clean}.

    Логика разбиения на бренд / товарную часть:
      1. Если в строке есть « - » (пробел-дефис-пробел) — слева бренд, справа товар.
      2. Иначе используется brand_hint.
      3. Иначе brand="" и вся строка идёт в product_part.

    Дефис в исходной строке нужен ТОЛЬКО для разделения. Он НЕ переносится
    в товарное название Ozon/WB (см. §7, §11.2 регламента).
    """
    text = (name or "").strip()
    parts = re.split(r"\s+-\s+", text, maxsplit=1)
    if len(parts) == 2:
        brand = parts[0].strip()
        product_part = parts[1].strip()
    else:
        brand = (brand_hint or "").strip()
        product_part = text

    volume = ""
    m = _VOLUME_RE.search(product_part)
    if m:
        n = m.group(1).replace(",", ".")
        unit = m.group(2).lower().replace("гр", "г").replace("gr", "г")
        volume = f"{n} {unit}"

    age_target = ""
    m_plus = _AGE_PLUS_RE.search(product_part)
    if m_plus:
        age_target = m_plus.group(1).strip()
    else:
        m_word = _AGE_WORD_RE.search(product_part)
        if m_word:
            age_target = m_word.group(1).strip()

    return {
        "brand": brand,
        "product_part": product_part,
        "volume": volume,
        "age_target": age_target,
        "name_clean": product_part,
    }


def format_ozon_title(parsed: dict, qty: int = 1) -> str:
    """Название Ozon: «[Набор N шт ]Brand product_part».

    §11.2 регламента: НЕТ дефиса между брендом и товаром (даже если в исходной
    строке он был). Для qty>1 в начало добавляется «Набор N шт» БЕЗ точки.
    """
    prefix = ""
    if qty == 2:
        prefix = "Набор 2 шт "
    elif qty == 3:
        prefix = "Набор 3 шт "
    elif qty > 3:
        prefix = f"Набор {qty} шт "

    brand = (parsed.get("brand") or "").strip()
    product = (parsed.get("product_part") or "").strip()
    if brand and product:
        title = f"{prefix}{brand} {product}"
    elif brand:
        title = f"{prefix}{brand}"
    else:
        title = f"{prefix}{product}"
    # Стираем возможные двойные пробелы и хвостовые знаки
    return re.sub(r"\s+", " ", title).strip()


def format_wb_short_title(parsed: dict, max_len: int = 60) -> str:
    """WB краткое: без бренда, ≤60 символов, без обрыва на середине слова.

    §12.2 регламента. Для qty>1 префикс «Набор N шт» НЕ добавляется в краткое
    наименование WB — там нет такой логики (наборность выражается через variants
    или отдельную группу).
    """
    product = (parsed.get("product_part") or "").strip()
    return _smart_trim(product, max_len)


def format_wb_full_title(parsed: dict, qty: int = 1) -> str:
    """WB полное наименование: с брендом. Для наборов добавляется префикс."""
    prefix = ""
    if qty == 2:
        prefix = "Набор 2 шт "
    elif qty == 3:
        prefix = "Набор 3 шт "
    elif qty > 3:
        prefix = f"Набор {qty} шт "
    brand = (parsed.get("brand") or "").strip()
    product = (parsed.get("product_part") or "").strip()
    if brand and product:
        out = f"{prefix}{brand} {product}"
    elif brand:
        out = f"{prefix}{brand}"
    else:
        out = f"{prefix}{product}"
    return re.sub(r"\s+", " ", out).strip()


def _smart_trim(s: str, max_len: int) -> str:
    """Обрезка по словам, не на середине слова. Если уже короче — возвращает как есть."""
    if len(s) <= max_len:
        return s
    cut = s[:max_len]
    last_space = cut.rfind(" ")
    # обрезаем по последнему пробелу если он не слишком в начале
    if last_space > int(max_len * 0.6):
        return cut[:last_space].rstrip(",.;:- ")
    return cut.rstrip(",.;:- ")


def wb_group_name(brand: str | None, subject_id: int) -> str:
    """§12.5 регламента: одна группа = один бренд + одна категория."""
    b = (brand or "").strip()
    if b and subject_id:
        return f"{b}_{subject_id}"
    if b:
        return b
    return f"sub_{subject_id}"


def ozon_group_name(brand: str | None, category_path: str) -> str:
    """§11.3 регламента: «Бренд - категория» (для Ozon допустим дефис, это не товарное название)."""
    b = (brand or "").strip() or "Brand"
    # берём последний сегмент пути категории как «категорию»
    cat = (category_path or "").split("/")[-1].strip() or "Категория"
    return f"{b} - {cat}"
