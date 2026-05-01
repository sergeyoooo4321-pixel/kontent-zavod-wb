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


def validate_ozon_item(item: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Pre-flight для Ozon-item (§11.6, §18.2 регламента).

    Возвращает (errors, warnings):
      • errors — критичные нарушения, SKU должен быть исключён из заливки.
      • warnings — некритичные замечания, складываются в state.warnings.
    """
    errors: list[str] = []
    warnings: list[str] = []

    name = (item.get("name") or "").strip()
    if not name:
        errors.append("name пустое")

    if not (item.get("offer_id") or ""):
        errors.append("offer_id пустой")

    if not item.get("category_id"):
        errors.append("category_id не задан")

    # НДС: храним как строку «0.22»
    vat = str(item.get("vat", ""))
    if vat not in ("0.22", "22", "0,22"):
        errors.append(f"vat={vat!r} должен быть 0.22 (22%)")

    if item.get("barcode"):
        errors.append("barcode заполнен — должен быть пустым")

    # Дефис между брендом и товаром в Ozon-названии (§11.2)
    if " - " in name and not name.startswith("Набор"):
        first = name.split(" - ", 1)[0].strip()
        if 1 <= len(first.split()) <= 3:
            errors.append(f"name содержит «{first} - …» — между брендом и товаром не должно быть дефиса")
    elif name.startswith("Набор") and " - " in name:
        rest = name.split(None, 3)
        if len(rest) >= 4 and " - " in rest[3]:
            tail_first = rest[3].split(" - ", 1)[0].strip()
            if 1 <= len(tail_first.split()) <= 3:
                errors.append(f"name «{name}»: дефис между брендом и товаром в наборе")

    # Аннотация: <6 предложений — warning (auto-fix должен дозаполнить),
    # пустая — критично.
    desc = item.get("description") or ""
    n_sent = _count_sentences(desc)
    if not desc.strip():
        errors.append("description пустое")
    elif n_sent < 6:
        warnings.append(f"description: {n_sent} предложений, должно быть ≥ 6")

    # Атрибуты — без них Ozon точно отвергнет
    attrs = item.get("attributes") or []
    if not attrs:
        errors.append("attributes пустой массив — обязательные характеристики не заполнены")

    # Габариты и вес
    for f in ("depth", "width", "height", "weight"):
        if not item.get(f):
            warnings.append(f"{f}={item.get(f)!r} — рекомендуется задать")

    # images
    if not (item.get("images") or []):
        errors.append("images пустой — нет ссылок на фото")

    return errors, warnings


def validate_ozon_item_qty(item: dict[str, Any], qty: int) -> tuple[list[str], list[str]]:
    """Доп. проверки на qty>1: в name обязательно префикс «Набор N шт»."""
    errors: list[str] = []
    warnings: list[str] = []
    name = (item.get("name") or "").strip()
    if qty == 2 and not name.startswith("Набор 2 шт"):
        errors.append("name должно начинаться с «Набор 2 шт»")
    if qty == 3 and not name.startswith("Набор 3 шт"):
        errors.append("name должно начинаться с «Набор 3 шт»")
    return errors, warnings


def validate_wb_imt(imt: dict[str, Any], brand: str | None = None) -> tuple[list[str], list[str]]:
    """Pre-flight для WB IMT (§12.8, §18.3 регламента).

    Возвращает (errors, warnings):
      • errors — субъект отсутствует, нет variants, vendorCode пустой,
        title пустой/>60/содержит бренд, dimensions нецелые/≤0, нет mediaFiles.
      • warnings — длинные characteristics-значения (возможно состав >100).
    """
    errors: list[str] = []
    warnings: list[str] = []

    subject_id = imt.get("subjectID")
    if not subject_id:
        errors.append("WB IMT: subjectID не задан")

    variants = imt.get("variants") or []
    if not variants:
        errors.append("WB IMT: нет variants")
        return errors, warnings

    for i, v in enumerate(variants):
        vp = f"variant[{i}]"

        if not v.get("vendorCode"):
            errors.append(f"{vp}: vendorCode пустой")

        title = (v.get("title") or "").strip()
        if not title:
            errors.append(f"{vp}: title пустой")
        elif len(title) > 60:
            errors.append(f"{vp}: title >{60} символов ({len(title)})")
        if brand and title and brand.strip() and brand.strip().lower() in title.lower():
            errors.append(f"{vp}: title содержит бренд «{brand}» (краткое наименование без бренда)")

        # Габариты — целые числа > 0
        dims = v.get("dimensions") or {}
        for f in ("length", "width", "height"):
            val = dims.get(f)
            if val is None:
                errors.append(f"{vp}.dimensions.{f}: пустое")
            elif not isinstance(val, int) and (isinstance(val, float) and val != int(val)):
                errors.append(f"{vp}.dimensions.{f}={val} не целое (WB ждёт целое)")
            elif (isinstance(val, (int, float)) and val <= 0):
                errors.append(f"{vp}.dimensions.{f}={val} ≤ 0")

        if not (v.get("mediaFiles") or []):
            errors.append(f"{vp}: mediaFiles пустой — нет ссылок на фото")

        # Состав ≤ 100 — пока warning, не error (только эвристика)
        chars = v.get("characteristics") or []
        for ch in chars:
            cid = ch.get("id")
            cval = ch.get("value")
            if isinstance(cval, str) and len(cval) > 100:
                warnings.append(f"{vp}.characteristics[id={cid}]: значение >{100} символов — возможно состав, нужно сократить")

    return errors, warnings


def smoke_summary(reports: dict[str, list[str]]) -> str:
    """Сводка для лога: dict[sku → list[errs]] → markdown-строка."""
    lines = []
    for sku, errs in reports.items():
        if errs:
            lines.append(f"• {sku}: {len(errs)} замечаний")
            for e in errs[:5]:
                lines.append(f"   – {e}")
    return "\n".join(lines) if lines else "(нет замечаний)"


# ─── Auto-fix helpers ────────────────────────────────────────────


def expand_short_description(text: str, brand: str | None, name: str, qty: int) -> str:
    """Достроить аннотацию до ≥6 предложений, если LLM выдала меньше.

    Добавляет общие осмысленные предложения (без галлюцинаций) — про комплект,
    качество, удобство покупки. Используется как safety net против отказа Ozon.
    """
    base = (text or "").strip()
    n = _count_sentences(base)
    if n >= 6:
        return base
    extras = []
    if qty > 1:
        extras.append(
            f"Этот вариант — комплект из {qty} единиц для удобной запаски и регулярного использования."
        )
        extras.append(
            f"Покупка набором экономит время и обеспечивает запас на длительный срок."
        )
    if brand:
        extras.append(f"Производитель — {brand}, известный своим качеством и проверенными решениями.")
    extras += [
        "Подходит для повседневного применения и удобен в хранении.",
        "Соответствует заявленным характеристикам и упаковке производителя.",
        "Доставка от продавца с маркетплейса в стандартные сроки.",
        "При вопросах по товару можно обратиться к продавцу через карточку.",
    ]
    needed = 6 - n
    selected = extras[:needed]
    if base and not base.endswith((".", "!", "?")):
        base = base + "."
    return (base + " " + " ".join(selected)).strip() if base else " ".join(selected)

