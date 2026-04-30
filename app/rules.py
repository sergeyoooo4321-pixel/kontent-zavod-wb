"""Бизнес-правила §5.2 ТЗ: расширение до 3 SKU, габариты, веса, лимиты, мультивыбор."""
from __future__ import annotations

import math
from typing import Any


# ─── базовые ──────────────────────────────────────────────────


def round_up(x: float) -> int:
    """Округление вверх до целого."""
    return math.ceil(x)


def round_to_hundred(g: int | float) -> int:
    """Вес в граммах: 97 → 100, 343 → 350, 405 → 410.

    Округляем вверх до ближайшего удобного целого: десятки если <100, полусотни/сотни выше.
    """
    g = math.ceil(g)
    if g < 50:
        return ((g + 4) // 5) * 5  # шаг 5
    if g < 200:
        return ((g + 9) // 10) * 10  # шаг 10
    if g < 1000:
        return ((g + 49) // 50) * 50  # шаг 50
    return ((g + 99) // 100) * 100  # шаг 100


def round_up_2dp(x: float) -> float:
    """Округление вверх до 2 знаков (для веса WB в кг)."""
    return math.ceil(x * 100) / 100


# ─── габариты ────────────────────────────────────────────────


def add_cm_to_dims(dims: dict[str, int | float], cm: int = 1) -> dict[str, int]:
    """ТЗ §5.2: если данные из интернета — +1 см к каждой стороне, округление вверх."""
    return {k: round_up(v + cm) for k, v in dims.items()}


def pack_dims(unit_dims: dict[str, int | float], qty: int) -> dict[str, int]:
    """Для наборов: меньшая сторона × количество, остальные — как у одиночки.

    Пример: единичный {l:10, w:5, h:3}, qty=3 → меньшая сторона h=3, h*3=9 → {l:10, w:5, h:9}.
    """
    if qty <= 1:
        return {k: round_up(v) for k, v in unit_dims.items()}
    items = sorted(unit_dims.items(), key=lambda kv: kv[1])
    smallest_key = items[0][0]
    return {
        k: round_up(v * qty if k == smallest_key else v) for k, v in unit_dims.items()
    }


# ─── расширение до 3 SKU ─────────────────────────────────────


def expand_to_3_skus(
    product: dict[str, Any],
    *,
    dims_from_internet: bool = False,
) -> list[dict[str, Any]]:
    """Возвращает 3 SKU: x1, x2, x3 со всеми пересчётами по §5.2.

    Каждый элемент: {sku, qty, name_suffix, weight_unit, weight_packed, dims, weight_wb_kg}.
    """
    base_sku = product["sku"]
    base_name = product["name"]
    base_weight = product.get("weight", 0) or 0
    base_dims = product.get("dims") or {"l": 10, "w": 10, "h": 10}

    if dims_from_internet:
        unit_dims = add_cm_to_dims(base_dims, 1)
    else:
        unit_dims = {k: round_up(v) for k, v in base_dims.items()}

    out = []
    for qty in (1, 2, 3):
        sku_suffix = "" if qty == 1 else f"x{qty}"
        sku = f"{base_sku}{sku_suffix}"
        name_suffix = "" if qty == 1 else f"Набор {qty} шт"
        weight_total_g = round_to_hundred(base_weight * qty) if base_weight else 0
        out.append({
            "sku": sku,
            "qty": qty,
            "name_suffix": name_suffix,
            "weight_unit_g": int(round_up(base_weight)) if base_weight else 0,
            "weight_packed_g": weight_total_g,
            "weight_wb_kg": round_up_2dp((base_weight * qty) / 1000) if base_weight else 0.01,
            "dims": pack_dims(unit_dims, qty),
        })
    return out


# ─── строки ──────────────────────────────────────────────────


def strip_brand(name: str, brand: str | None) -> str:
    """Убрать бренд из названия (для WB title_short)."""
    if not brand:
        return name
    out = name
    for token in (brand, brand.lower(), brand.upper(), brand.capitalize()):
        out = out.replace(token, "").strip()
    return " ".join(out.split())  # схлопнуть множественные пробелы


def limit_chars(s: str, n: int) -> str:
    """Обрезка с многоточием при необходимости."""
    if len(s) <= n:
        return s
    if n <= 1:
        return s[:n]
    return s[: n - 1].rstrip() + "…"


def nds_value() -> int:
    """ТЗ §5.2: НДС везде 22%."""
    return 22


# ─── справочники / мультивыбор ───────────────────────────────


def join_multivalue(values: list[str]) -> str:
    """Мультивыбор склеиваем через `;` без пробелов (ТЗ §5.1)."""
    return ";".join(v.strip() for v in values if v and v.strip())


def _levenshtein(a: str, b: str) -> int:
    """Расстояние Левенштейна (для подбора ближайшего значения)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur.append(min(cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost))
        prev = cur
    return prev[-1]


def pick_from_dict(
    dict_values: list[str],
    raw: str,
    *,
    case_insensitive: bool = True,
) -> tuple[str | None, bool]:
    """Возвращает (value, was_substituted).

    1) Если raw точно есть в перечне — (raw, False).
    2) Иначе — ближайшее по Левенштейну (либо None если перечень пуст). was_substituted=True.
    """
    if not dict_values:
        return None, True
    if raw in dict_values:
        return raw, False
    if case_insensitive:
        norm = {v.lower(): v for v in dict_values}
        if raw.lower() in norm:
            return norm[raw.lower()], False
    # ближайшее по Левенштейну
    best = min(dict_values, key=lambda v: _levenshtein(raw.lower() if case_insensitive else raw,
                                                        v.lower() if case_insensitive else v))
    return best, True
