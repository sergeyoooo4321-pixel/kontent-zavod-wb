"""Маппинг LLM-сырых значений атрибутов в формат Ozon/WB API.

Pipeline:
  LLM возвращает { "<id>": "сырое значение" | ["...", "..."] }
  Локально через rules.pick_from_dict (Левенштейн) находим ближайшее значение
  справочника и собираем payload в формате конкретного API.

Если required-атрибут не получил значения или оно не нашлось в словаре —
возвращаем (None, warnings) — вызывающий должен исключить SKU из импорта.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from .rules import pick_from_dict

logger = logging.getLogger(__name__)

_NUM_RE = re.compile(r"-?\d+(?:[.,]\d+)?")
_RANGE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*[-–—]\s*(\d+(?:[.,]\d+)?)")
_UPTO_RE = re.compile(r"(?:до|менее|less\s+than|до\s+)\s*(\d+(?:[.,]\d+)?)", re.IGNORECASE)
_FROM_RE = re.compile(r"(?:от|более|больше|свыше|over|more\s+than)\s*(\d+(?:[.,]\d+)?)", re.IGNORECASE)


def _try_number(s: str) -> float | None:
    m = _NUM_RE.search(str(s))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _match_numeric_range(value: float, dict_strings: list[str]) -> str | None:
    """Если LLM выдала число, а словарь — диапазоны («до 500», «500-1000», «от 1000»),
    подбираем подходящий диапазон по значению.
    Используется когда pick_from_dict не нашёл точного совпадения.
    """
    for s in dict_strings:
        m = _RANGE_RE.search(s)
        if m:
            lo = float(m.group(1).replace(",", "."))
            hi = float(m.group(2).replace(",", "."))
            if lo <= value <= hi:
                return s
        m = _UPTO_RE.search(s)
        if m:
            hi = float(m.group(1).replace(",", "."))
            if value <= hi:
                return s
        m = _FROM_RE.search(s)
        if m:
            lo = float(m.group(1).replace(",", "."))
            if value >= lo:
                return s
    return None


def _match_closest_numeric(value: float, dict_strings: list[str]) -> str | None:
    """Если словарь — список конкретных чисел («100», «200», «500», «1000»),
    выбираем ближайшее к value по абсолютной разнице.

    Используется когда ни точное совпадение (pick_from_dict), ни диапазонный
    матч (_match_numeric_range) не сработали. Возвращает None если в словаре
    нет ни одного числа.
    """
    best_str: str | None = None
    best_diff: float | None = None
    for s in dict_strings:
        n = _try_number(s)
        if n is None:
            continue
        d = abs(n - value)
        if best_diff is None or d < best_diff:
            best_diff = d
            best_str = s
    return best_str


def _to_list(raw: Any) -> list[str]:
    """Нормализация LLM-значения к списку строк."""
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if x not in (None, "") and str(x).strip()]
    s = str(raw).strip()
    return [s] if s else []


def _resolve_value(
    raw: str,
    dict_strings: list[str],
    warnings: list[str],
    ctx: str,
) -> tuple[str | None, bool]:
    """Найти значение из словаря под LLM-сырое.

    Порядок:
      1. Если raw — число и словарь содержит диапазоны — numeric-range.
      2. Если raw — число и словарь содержит конкретные числа — closest-numeric.
      3. Иначе — Левенштейн (rules.pick_from_dict).

    Числовое сопоставление приоритетнее, потому что Левенштейн «300» → «100»
    выбирает по символам, а правильно — по значению (ближайшее = «250»).

    Возвращает (matched, was_substituted). matched=None если ничего не нашлось.
    Все substituted-случаи добавляются в `warnings` с правильным тегом.
    """
    num = _try_number(raw)
    if num is not None and dict_strings:
        ranged = _match_numeric_range(num, dict_strings)
        if ranged is not None:
            warnings.append(f"{ctx}: '{raw}' → диапазон '{ranged}' (numeric-range)")
            return ranged, True
        # closest-numeric — только если в словаре есть числа
        closest = _match_closest_numeric(num, dict_strings)
        if closest is not None:
            warnings.append(f"{ctx}: '{raw}' → ближайшее '{closest}' (closest-numeric)")
            return closest, True

    matched, was_sub = pick_from_dict(dict_strings, raw)
    if matched is not None and was_sub:
        warnings.append(f"{ctx}: '{raw}' → '{matched}' (substituted)")
    return matched, was_sub


def map_ozon_attributes(
    llm_values: dict[str, Any],
    ozon_attrs: list[dict],
    ozon_attr_values: dict[int, list[dict]],
    *,
    brand_hint: str | None = None,
    country_hint: str = "Россия",
) -> tuple[list[dict] | None, list[str]]:
    """Собирает attributes[] для Ozon /v3/product/import.

    Структура одного атрибута:
        {
          "complex_id": int,
          "id": <attribute_id>,
          "values": [
            {"dictionary_value_id": <id>, "value": "..."}  # для словарных
            или {"value": "..."}                            # для строки/числа
          ]
        }
    """
    out: list[dict] = []
    warnings: list[str] = []

    for a in ozon_attrs:
        attr_id = int(a.get("id") or 0)
        if not attr_id:
            continue
        name = a.get("name") or str(attr_id)
        required = bool(a.get("is_required") or a.get("required"))
        is_collection = bool(a.get("is_collection"))
        dict_id = a.get("dictionary_id") or 0
        complex_id = int(a.get("attribute_complex_id") or 0)

        raw = llm_values.get(str(attr_id))
        if raw is None:
            raw = llm_values.get(attr_id)
        raws = _to_list(raw)
        if not is_collection:
            raws = raws[:1]

        # Auto-fill для типичных полей которые LLM пропускает
        if not raws:
            name_l = name.lower()
            # Ozon attr id=85 «Бренд» — берём из brand_hint
            if (attr_id == 85 or "бренд" in name_l) and brand_hint:
                raws = [brand_hint]
                warnings.append(f"ozon attr {name}: auto-filled from brand_hint='{brand_hint}'")
            # «Страна-изготовитель» — Россия по умолчанию
            elif "стран" in name_l and ("изготов" in name_l or "произв" in name_l):
                raws = [country_hint]
                warnings.append(f"ozon attr {name}: auto-filled with default '{country_hint}'")

        if not raws:
            if required:
                return None, warnings + [f"attr {name} (#{attr_id}) required, missing"]
            continue

        values_payload: list[dict] = []
        if dict_id:
            vals = ozon_attr_values.get(attr_id, [])
            dict_strings = [v.get("value") for v in vals if v.get("value")]
            for r in raws:
                matched, was_sub = _resolve_value(r, dict_strings, warnings,
                                                  ctx=f"ozon attr {name}")
                if matched is None:
                    if required:
                        return None, warnings + [
                            f"attr {name} (#{attr_id}): '{r}' not in dict"
                        ]
                    continue
                vid = next(
                    (v.get("id") for v in vals if v.get("value") == matched),
                    None,
                )
                if vid is None:
                    continue
                values_payload.append({
                    "dictionary_value_id": int(vid),
                    "value": matched,
                })
        else:
            for r in raws:
                values_payload.append({"value": r})

        if not values_payload:
            if required:
                return None, warnings + [f"attr {name} (#{attr_id}): no resolved values"]
            continue

        out.append({
            "complex_id": complex_id,
            "id": attr_id,
            "values": values_payload,
        })

    return out, warnings


def map_wb_characteristics(
    llm_values: dict[str, Any],
    wb_charcs: list[dict],
    wb_charc_values: dict[int, list[dict]],
    *,
    brand_hint: str | None = None,
    country_hint: str = "Россия",
) -> tuple[list[dict] | None, list[str]]:
    """Собирает characteristics[] для WB /content/v2/cards/upload.

    Структура: {"id": <charcID>, "value": [<v1>, <v2>, ...]}.
    charcType: 0=number, 1=string, 4=dictionary_single, 5=dictionary_multi.
    """
    out: list[dict] = []
    warnings: list[str] = []

    for c in wb_charcs:
        cid = int(c.get("charcID") or c.get("id") or 0)
        if not cid:
            continue
        name = c.get("name") or str(cid)
        required = bool(c.get("required") or c.get("isRequired"))
        ctype = c.get("charcType")
        max_count = int(c.get("maxCount") or 0)

        raw = llm_values.get(str(cid))
        if raw is None:
            raw = llm_values.get(cid)
        raws = _to_list(raw)
        if max_count:
            raws = raws[:max_count]

        # Auto-fill для типичных полей если LLM пропустила
        if not raws:
            name_l = name.lower()
            if "бренд" in name_l and brand_hint:
                raws = [brand_hint]
                warnings.append(f"wb charc {name}: auto-filled from brand_hint='{brand_hint}'")
            elif "стран" in name_l and ("изготов" in name_l or "произв" in name_l):
                raws = [country_hint]
                warnings.append(f"wb charc {name}: auto-filled with default '{country_hint}'")

        if not raws:
            if required:
                return None, warnings + [f"charc {name} (#{cid}) required, missing"]
            continue

        resolved: list[Any] = []
        if ctype in (4, 5):
            vals = wb_charc_values.get(cid, [])
            dict_strings = [v.get("name") for v in vals if v.get("name")]
            for r in raws:
                matched, _was = _resolve_value(r, dict_strings, warnings,
                                               ctx=f"wb charc {name}")
                if matched is None:
                    if required:
                        return None, warnings + [
                            f"charc {name} (#{cid}): '{r}' not in dict"
                        ]
                    continue
                resolved.append(matched)
        elif ctype == 0:
            for r in raws:
                try:
                    resolved.append(int(float(r)))
                except (TypeError, ValueError):
                    if required:
                        return None, warnings + [
                            f"charc {name} (#{cid}): '{r}' not number"
                        ]
        else:
            for r in raws:
                resolved.append(r)

        if not resolved:
            if required:
                return None, warnings + [f"charc {name} (#{cid}): empty after resolution"]
            continue

        out.append({"id": cid, "value": resolved})

    return out, warnings
