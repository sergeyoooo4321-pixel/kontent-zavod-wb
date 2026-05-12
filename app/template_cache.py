"""Кеш пустых xlsx-шаблонов от юзера.

Юзер один раз скачивает пустой шаблон в кабинете МП (Ozon — конкретно
для категории, WB — для предмета) и кидает в чат. Мы парсим, извлекаем
category_id / subject_id и кладём в `templates/<cabinet>/<mp>/<id>/`:
  - template.xlsx   — оригинал пустого шаблона
  - meta.json       — распарсенная структура (TemplateSpec)
  - parsed_at       — timestamp последнего обновления

Lookup в make_batch_zip: для каждой определённой LLM'ом категории
вызываем find_template(cabinet, mp, id) → (xlsx_path, json_path) | None.
Если None — гном просит юзера один раз скинуть шаблон.

Кеш переживает рестарт и накапливается со временем. На 50% категорий
юзеру никогда не придётся качать шаблон повторно.
"""
from __future__ import annotations

import json
import logging
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_ROOT = Path.home() / "cz-backend" / "templates"


@dataclass
class CachedTemplate:
    cabinet: str
    marketplace: str          # "ozon" / "wb"
    category_id: int          # для Ozon — description_category_id; для WB — subjectID
    xlsx_path: str            # абсолютный путь
    json_path: str
    parsed_at: int            # unix ts


def _dir_for(cabinet: str, mp: str, category_id: int) -> Path:
    return _ROOT / (cabinet or "default") / mp / str(category_id)


def find_template(
    cabinet: str | None,
    mp: str,
    category_id: int,
) -> CachedTemplate | None:
    """Найти кешированный шаблон по (cabinet, mp, category_id).

    Если не найдено в указанном кабинете — пробуем default-кабинет (общий
    кеш). Идея: шаблон от продавца можно использовать в любом его кабинете,
    структура одинаковая.
    """
    for cab_try in (cabinet, "default"):
        if not cab_try:
            continue
        d = _dir_for(cab_try, mp, category_id)
        xlsx = d / "template.xlsx"
        meta = d / "meta.json"
        if xlsx.exists() and meta.exists():
            try:
                ts = int(meta.stat().st_mtime)
            except Exception:
                ts = 0
            return CachedTemplate(
                cabinet=cab_try,
                marketplace=mp,
                category_id=category_id,
                xlsx_path=str(xlsx),
                json_path=str(meta),
                parsed_at=ts,
            )
    return None


def save_template(
    *,
    cabinet: str | None,
    source_xlsx: str | Path,
    parsed_meta_json: str | Path | None = None,
    parsed_dict: dict | None = None,
    marketplace: str,
    category_id: int,
) -> CachedTemplate:
    """Положить xlsx и его распарсенный мета-JSON в кеш.

    Один из `parsed_meta_json` (путь к уже-сохранённому JSON) или
    `parsed_dict` (структура от parse_template) должен быть задан.
    """
    if not category_id:
        raise ValueError("category_id обязателен для кеша")
    if marketplace not in ("ozon", "wb"):
        raise ValueError(f"marketplace должен быть ozon/wb, получил {marketplace!r}")

    cab = cabinet or "default"
    d = _dir_for(cab, marketplace, category_id)
    d.mkdir(parents=True, exist_ok=True)

    dst_xlsx = d / "template.xlsx"
    dst_meta = d / "meta.json"

    src = Path(source_xlsx).expanduser()
    if not src.exists():
        raise FileNotFoundError(f"source xlsx не найден: {src}")
    shutil.copyfile(src, dst_xlsx)

    if parsed_meta_json is not None:
        src_meta = Path(parsed_meta_json).expanduser()
        if src_meta.exists():
            shutil.copyfile(src_meta, dst_meta)
        else:
            raise FileNotFoundError(f"meta json не найден: {src_meta}")
    elif parsed_dict is not None:
        dst_meta.write_text(
            json.dumps(parsed_dict, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    else:
        raise ValueError("нужно передать parsed_meta_json или parsed_dict")

    ts = int(time.time())
    logger.info("template_cache: saved %s/%s/%s", cab, marketplace, category_id)
    return CachedTemplate(
        cabinet=cab,
        marketplace=marketplace,
        category_id=category_id,
        xlsx_path=str(dst_xlsx),
        json_path=str(dst_meta),
        parsed_at=ts,
    )


def list_all() -> list[CachedTemplate]:
    """Все шаблоны в кеше — для админ-обзора."""
    out: list[CachedTemplate] = []
    if not _ROOT.exists():
        return out
    for cab_dir in _ROOT.iterdir():
        if not cab_dir.is_dir():
            continue
        for mp_dir in cab_dir.iterdir():
            mp = mp_dir.name
            if mp not in ("ozon", "wb"):
                continue
            for cat_dir in mp_dir.iterdir():
                try:
                    cid = int(cat_dir.name)
                except ValueError:
                    continue
                xlsx = cat_dir / "template.xlsx"
                meta = cat_dir / "meta.json"
                if xlsx.exists() and meta.exists():
                    try:
                        ts = int(meta.stat().st_mtime)
                    except Exception:
                        ts = 0
                    out.append(CachedTemplate(
                        cabinet=cab_dir.name,
                        marketplace=mp,
                        category_id=cid,
                        xlsx_path=str(xlsx),
                        json_path=str(meta),
                        parsed_at=ts,
                    ))
    return out
