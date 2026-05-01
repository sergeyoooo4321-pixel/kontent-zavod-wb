"""Persistent кейс-лог для накопления опыта (§6, §7 ТЗ).

Идея: после каждого прогона записываем по строке-JSON на каждый SKU:
  - что сгенерилось (фото, тайтлы)
  - какие значения подставили в атрибуты/характеристики
  - где сработал fallback по Левенштейну (warnings)
  - какие ошибки вернул МП

Файлы: ~/cz-backend/cases/{YYYY-MM-DD}.jsonl  (per-day rotation, append-only)

Формат — JSONL (одна строка = один JSON-объект). Удобен для:
  - tail -f во время прогона
  - jq-фильтрации: cat 2026-05-01.jsonl | jq 'select(.errors | length > 0)'
  - последующего обучения (структурированная база ошибок маппинга)
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Папка можно переопределить через env CZ_CASES_DIR; по умолчанию ~/cz-backend/cases.
_CASES_DIR_ENV = "CZ_CASES_DIR"
_DEFAULT_DIR = Path.home() / "cz-backend" / "cases"


def _cases_dir() -> Path:
    return Path(os.environ.get(_CASES_DIR_ENV) or _DEFAULT_DIR)


def _today_file() -> Path:
    d = _cases_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl"


def write_case(entry: dict[str, Any]) -> None:
    """Записать одну строку в дневной jsonl. Безопасно — не падает на ошибках I/O."""
    try:
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with _today_file().open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        logger.warning("case_log write failed: %s", e)


def write_batch_summary(
    *,
    batch_id: str,
    chat_id: int,
    cabinet_names: list[str] | None,
    products: list[dict[str, Any]],
    successes: list[Any],
    errors: list[Any],
    warnings: list[Any],
) -> None:
    """Запись по итогам run_batch — один объект на партию + по объекту на SKU."""
    now = datetime.now(timezone.utc).isoformat()

    # Сводка партии
    write_case({
        "ts": now,
        "type": "batch",
        "batch_id": batch_id,
        "chat_id": chat_id,
        "cabinets": cabinet_names or [],
        "n_products": len(products),
        "n_successes": len(successes),
        "n_errors": len(errors),
        "n_warnings": len(warnings),
    })

    # Кейс на каждое успешное / отказанное / с warning SKU
    for it in successes:
        write_case({
            "ts": now,
            "type": "sku_success",
            "batch_id": batch_id,
            "sku": getattr(it, "sku", None),
            "mp": getattr(it, "mp", None),
            "marketplace_id": getattr(it, "marketplace_id", None),
        })
    for it in errors:
        write_case({
            "ts": now,
            "type": "sku_error",
            "batch_id": batch_id,
            "sku": getattr(it, "sku", None),
            "mp": getattr(it, "mp", None),
            "field": getattr(it, "field", None),
            "reason": getattr(it, "reason", None),
        })
    for it in warnings:
        write_case({
            "ts": now,
            "type": "sku_warning",
            "batch_id": batch_id,
            "sku": getattr(it, "sku", None),
            "mp": getattr(it, "mp", None),
            "field": getattr(it, "field", None),
            "reason": getattr(it, "reason", None),
        })


def write_product_state(
    *,
    batch_id: str,
    state: Any,  # ProductState
) -> None:
    """Запись по одному ProductState — что подставили в карточке.

    Это самое ценное для обучения: видим какие значения LLM выдала, что
    Левенштейн подменил, как выглядит финальный набор атрибутов.
    """
    try:
        write_case({
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": "product_state",
            "batch_id": batch_id,
            "sku": state.sku,
            "name": state.name,
            "brand": state.brand,
            "src_url": state.src_url,
            "images": state.images,
            "ozon_category": (
                {"id": state.ozon_category.id, "path": state.ozon_category.path}
                if state.ozon_category else None
            ),
            "wb_subject": (
                {"id": state.wb_subject.id, "path": state.wb_subject.path}
                if state.wb_subject else None
            ),
            "skus_3": state.skus_3,
            "titles": state.titles,
            "attributes_ozon": state.attributes_ozon,
            "characteristics_wb": state.characteristics_wb,
            "errors": state.errors,
            "warnings": state.warnings,
        })
    except Exception as e:
        logger.warning("case_log product_state failed sku=%s: %s", getattr(state, "sku", None), e)
