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
from typing import Any

from .rules import pick_from_dict

logger = logging.getLogger(__name__)


def _to_list(raw: Any) -> list[str]:
    """Нормализация LLM-значения к списку строк."""
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if x not in (None, "") and str(x).strip()]
    s = str(raw).strip()
    return [s] if s else []


def map_ozon_attributes(
    llm_values: dict[str, Any],
    ozon_attrs: list[dict],
    ozon_attr_values: dict[int, list[dict]],
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

        if not raws:
            if required:
                return None, warnings + [f"attr {name} (#{attr_id}) required, missing"]
            continue

        values_payload: list[dict] = []
        if dict_id:
            vals = ozon_attr_values.get(attr_id, [])
            dict_strings = [v.get("value") for v in vals if v.get("value")]
            for r in raws:
                matched, was_sub = pick_from_dict(dict_strings, r)
                if matched is None:
                    if required:
                        return None, warnings + [
                            f"attr {name} (#{attr_id}): '{r}' not in dict"
                        ]
                    continue
                if was_sub:
                    warnings.append(f"ozon attr {name}: '{r}' → '{matched}' (substituted)")
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

        if not raws:
            if required:
                return None, warnings + [f"charc {name} (#{cid}) required, missing"]
            continue

        resolved: list[Any] = []
        if ctype in (4, 5):
            vals = wb_charc_values.get(cid, [])
            dict_strings = [v.get("name") for v in vals if v.get("name")]
            for r in raws:
                matched, was_sub = pick_from_dict(dict_strings, r)
                if matched is None:
                    if required:
                        return None, warnings + [
                            f"charc {name} (#{cid}): '{r}' not in dict"
                        ]
                    continue
                if was_sub:
                    warnings.append(f"wb charc {name}: '{r}' → '{matched}' (substituted)")
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
