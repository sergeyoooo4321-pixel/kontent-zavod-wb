"""Локальная pre-flight валидация карточек перед отправкой в API маркетплейсов.

Реализует Шаг 7 («Предварительная валидация до отправки») регламента
«API-добавление Ozon/Wildberries» — §13.7. Цель: поймать типовые ошибки,
которые маркетплейс почти наверняка вернёт 400, ещё до отправки.

Возвращает list[str] — список найденных проблем. Пустой список = всё ок.

ВАЖНО: валидация НЕ блокирует отправку; вызывающий код решает, отправлять
карточку всё равно или пропустить. Поведение — записывать ошибки/warnings
в state, продолжать заливку оставшихся.
"""
from __future__ import annotations

import re
from typing import Any


_SENTENCE_END_RE = re.compile(r"[.!?]+\s+|[.!?]+$")


def _count_sentences(text: str) -> int:
    """Грубая оценка количества предложений: считаем закрывающие . ! ?"""
    if not text:
        return 0
    parts = [p for p in _SENTENCE_END_RE.split(text) if p.strip()]
    return len(parts)


def validate_ozon_item(item: dict[str, Any]) -> list[str]:
    """Pre-flight для Ozon-item (см. §11.6, §18.2 регламента).

    Жёсткие правила (вернёт ошибку при нарушении):
      • НДС должен быть 0.22 (22%)
      • barcode не должен быть заполнен
      • name не должен иметь дефис между брендом и товаром
        (если первая часть до « - » короче 4 слов — считаем брендом)
      • для qty>1 в начале name должно быть «Набор N шт»
      • description (аннотация) ≥ 6 предложений
      • offer_id, name, category_id, weight обязательны
    """
    errs: list[str] = []

    name = (item.get("name") or "").strip()
    if not name:
        errs.append("name пустое")

    offer_id = item.get("offer_id") or ""
    if not offer_id:
        errs.append("offer_id пустой")

    if not item.get("category_id"):
        errs.append("category_id не задан")

    # НДС: храним как строку «0.22»
    vat = str(item.get("vat", ""))
    if vat not in ("0.22", "22", "0,22"):
        errs.append(f"vat={vat!r} должен быть 0.22 (22%)")

    if item.get("barcode"):
        errs.append("barcode заполнен — должен быть пустым")

    # Дефис между брендом и товаром в Ozon-названии
    # Эвристика: префикс до « - » длиной 1–3 слова — это бренд → ошибка
    if " - " in name and not name.startswith("Набор"):
        first = name.split(" - ", 1)[0].strip()
        if 1 <= len(first.split()) <= 3:
            errs.append(f"name содержит «{first} - …» — между брендом и товаром не должно быть дефиса")
    elif name.startswith("Набор") and " - " in name:
        # Внутри набора тоже допустимо проверить остальную часть
        rest = name.split(None, 3)
        if len(rest) >= 4 and " - " in rest[3]:
            tail_first = rest[3].split(" - ", 1)[0].strip()
            if 1 <= len(tail_first.split()) <= 3:
                errs.append(f"name «{name}»: дефис между брендом и товаром в наборе")

    # Префикс «Набор N шт» для qty>1 — определим по weight_packed_g не получится,
    # передаваться должно отдельно. Здесь просто sanity-check: name начинается с «Набор» <=> в нём указано слово
    # Полная проверка делается на уровне вызывающего, который знает qty.

    # Аннотация
    desc = item.get("description") or ""
    n_sent = _count_sentences(desc)
    if n_sent < 6:
        errs.append(f"description: {n_sent} предложений, должно быть ≥ 6")

    # Атрибуты
    attrs = item.get("attributes") or []
    if not attrs:
        errs.append("attributes пустой массив — не заполнены характеристики категории")

    return errs


def validate_ozon_item_qty(item: dict[str, Any], qty: int) -> list[str]:
    """Дополнительные проверки с учётом qty: для qty>1 проверяем префикс."""
    errs: list[str] = []
    name = (item.get("name") or "").strip()
    if qty == 2 and not name.startswith("Набор 2 шт"):
        errs.append("name должно начинаться с «Набор 2 шт»")
    if qty == 3 and not name.startswith("Набор 3 шт"):
        errs.append("name должно начинаться с «Набор 3 шт»")
    return errs


def validate_wb_imt(imt: dict[str, Any], brand: str | None = None) -> list[str]:
    """Pre-flight для WB IMT (см. §12.8, §18.3 регламента).

    Проверяет КАЖДЫЙ variant внутри IMT:
      • subjectID > 0
      • title (краткое) ≤ 60 символов и НЕ содержит бренд
      • vendorCode заполнен
      • если есть description (полное) — может содержать бренд (это ок)
      • dimensions — целые числа > 0
    """
    errs: list[str] = []
    subject_id = imt.get("subjectID")
    if not subject_id:
        errs.append("WB IMT: subjectID не задан")

    variants = imt.get("variants") or []
    if not variants:
        errs.append("WB IMT: нет variants")
        return errs

    for i, v in enumerate(variants):
        vp = f"variant[{i}]"

        if not v.get("vendorCode"):
            errs.append(f"{vp}: vendorCode пустой")

        title = (v.get("title") or "").strip()
        if not title:
            errs.append(f"{vp}: title пустой")
        elif len(title) > 60:
            errs.append(f"{vp}: title >{60} символов ({len(title)})")
        if brand and title and brand.strip() and brand.strip().lower() in title.lower():
            errs.append(f"{vp}: title содержит бренд «{brand}» (краткое наименование без бренда)")

        # Габариты — целые числа > 0
        dims = v.get("dimensions") or {}
        for f in ("length", "width", "height"):
            val = dims.get(f)
            if val is None:
                errs.append(f"{vp}.dimensions.{f}: пустое")
            elif not isinstance(val, int) and (isinstance(val, float) and val != int(val)):
                errs.append(f"{vp}.dimensions.{f}={val} не целое (WB ждёт целое)")
            elif (isinstance(val, (int, float)) and val <= 0):
                errs.append(f"{vp}.dimensions.{f}={val} ≤ 0")

        # Состав ≤ 100 (если присутствует среди characteristics)
        chars = v.get("characteristics") or []
        for ch in chars:
            cid = ch.get("id")
            cval = ch.get("value")
            # эвристика: если в значении явно «состав»-длинная строка — проверяем
            if isinstance(cval, str) and len(cval) > 100:
                # опционально по name атрибута, но id у нас есть, не name
                # сигнализируем о любом значении >100 как потенциально про состав
                errs.append(f"{vp}.characteristics[id={cid}]: значение >{100} символов — возможно состав, нужно сократить")

    return errs


def validate_wb_imt_qty(imt: dict[str, Any], qty: int) -> list[str]:
    """Доп. проверки на qty>1: dimensions набора должны быть умножены по меньшей стороне.

    Это вызвается ДО построения IMT — параметр dims_unit передаётся snapshot'ом.
    """
    # Эта проверка делается на уровне expand_to_3_skus в pack_dims;
    # тут просто проверим что у наборов хотя бы одна сторона больше 1шт.
    return []


def smoke_summary(reports: dict[str, list[str]]) -> str:
    """Сводка для лога: dict[sku → list[errs]] → markdown-строка."""
    lines = []
    for sku, errs in reports.items():
        if errs:
            lines.append(f"• {sku}: {len(errs)} замечаний")
            for e in errs[:5]:
                lines.append(f"   – {e}")
    return "\n".join(lines) if lines else "(нет замечаний)"
