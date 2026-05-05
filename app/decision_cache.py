"""Persistent cache юзерских решений по атрибутам товаров.

После каждой Excel-партии fill_excel_batch записывает в JSONL ответы,
которые юзер вручную дал на pending-вопросы (категория, цвет, ТНВЭД и т.п.).
В следующей партии с тем же товаром (тот же бренд + первые 3 значимых слова
имени) кеш отдаёт сохранённые ответы — и pending-вопросы по этим полям
не задаются.

Файлы: ~/cz-backend/decisions/<cabinet>/<marketplace>.jsonl
Формат строки:
  {"ts": "...", "brand": "Tide", "name_root": "стиральный порошок альпийская",
   "answers": {"Цвет": "Белый", "ТНВЭД": "..."}}

Чтение: грепаем целиком файл по (brand, name_root). Последние записи
переопределяют старые (мерджим в читающую сторону).
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

_DEFAULT_DIR = Path.home() / "cz-backend" / "decisions"

# Стоп-слова, бесполезные для идентификации товара
_STOP = frozenset({
    "для", "под", "при", "над", "без", "из", "за", "по", "на", "в", "к", "с",
    "и", "или", "the", "and", "set", "kit", "of", "a", "an",
})


def _decisions_dir() -> Path:
    import os
    custom = os.environ.get("CZ_DECISIONS_DIR")
    return Path(custom) if custom else _DEFAULT_DIR


def _significant_words(text: str, n: int = 3) -> list[str]:
    """Первые N значимых слов из строки (lowercase, без стопов, без чисел).

    Используется для name_root: «Стиральный порошок Альпийская свежесть 400 г»
    → ["стиральный", "порошок", "альпийская"].
    """
    words = re.findall(r"[\wа-яА-ЯёЁ]+", (text or "").lower())
    out: list[str] = []
    for w in words:
        if len(w) < 3:
            continue
        if w in _STOP:
            continue
        if w.isdigit():
            continue
        out.append(w)
        if len(out) >= n:
            break
    return out


def _make_key(brand: str, name: str) -> tuple[str, str]:
    """Возвращает (brand_norm, name_root) — нормализованный ключ.

    name_root вычисляется БЕЗ слов бренда — чтобы «Tide Стиральный порошок»
    и «Стиральный порошок» (без префикса бренда) попадали в один ключ.
    """
    brand_norm = (brand or "").strip().lower()
    text = (name or "")
    if brand_norm:
        # Удаляем все вхождения бренда (case-insensitive) из имени
        for token in (brand, brand.lower(), brand.upper(), brand.capitalize()):
            if token:
                text = text.replace(token, " ")
    name_root = " ".join(_significant_words(text, n=3))
    return brand_norm, name_root


def _file_for(cabinet: str, marketplace: str) -> Path:
    cab = (cabinet or "default").strip() or "default"
    mp = (marketplace or "common").strip() or "common"
    p = _decisions_dir() / cab / f"{mp}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _iter_records(path: Path) -> Iterable[dict]:
    if not path.exists():
        return
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        logger.warning("decision_cache read %s failed: %s", path, e)


def read_cached_answers(
    cabinet: str,
    marketplace: str,
    brand: str,
    name: str,
) -> dict[str, str]:
    """Возвращает накопленные ответы для (brand, name_root).

    Если несколько записей одного ключа — последние перекрывают ранние.
    Пустой dict если ничего не нашлось.
    """
    brand_n, name_n = _make_key(brand, name)
    if not (brand_n and name_n):
        return {}
    path = _file_for(cabinet, marketplace)
    merged: dict[str, str] = {}
    for rec in _iter_records(path):
        if rec.get("brand") != brand_n:
            continue
        if rec.get("name_root") != name_n:
            continue
        ans = rec.get("answers") or {}
        if isinstance(ans, dict):
            for k, v in ans.items():
                if isinstance(k, str) and v not in (None, ""):
                    merged[k] = str(v)
    return merged


def append_cache(
    cabinet: str,
    marketplace: str,
    brand: str,
    name: str,
    answers: dict[str, str],
) -> None:
    """Append одну строку JSONL с answers для (brand, name_root).

    answers — словарь {field_name: answer}. Пустые значения отфильтруются.
    Если answers пустой — не пишем.
    """
    brand_n, name_n = _make_key(brand, name)
    if not (brand_n and name_n):
        return
    cleaned = {k: str(v) for k, v in (answers or {}).items()
               if isinstance(k, str) and v not in (None, "")}
    if not cleaned:
        return
    path = _file_for(cabinet, marketplace)
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "brand": brand_n,
        "name_root": name_n,
        "answers": cleaned,
    }
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("decision_cache write %s failed: %s", path, e)
