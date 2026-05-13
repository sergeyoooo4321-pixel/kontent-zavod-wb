from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from app.config import Settings
from app.marketplace_clients import OzonClient, WBClient
from app.models import CategoryMatch, MarketplaceFieldValue, MarketplaceProfile, ProductInput


logger = logging.getLogger(__name__)


STOP_WORDS = {
    "для",
    "без",
    "под",
    "при",
    "или",
    "это",
    "набор",
    "штук",
    "шт",
    "гр",
    "мл",
    "ozon",
    "wildberries",
    "wb",
    "the",
    "and",
    "for",
}


@dataclass(frozen=True)
class Candidate:
    marketplace: str
    id: int
    type_id: int | None
    path: str
    score: float


class MarketplaceResolver:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.cache_dir = settings.RUNTIME_DIR / "marketplace-cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._ozon = OzonClient(settings) if settings.ozon_credentials and settings.MARKETPLACE_LIVE_ENABLED else None
        self._wb = WBClient(settings) if settings.wb_token and settings.MARKETPLACE_LIVE_ENABLED else None

    async def resolve(self, product: ProductInput) -> MarketplaceProfile:
        profile = MarketplaceProfile()
        if not self.settings.MARKETPLACE_LIVE_ENABLED:
            profile.warnings.append("marketplace live API is disabled")
            return profile

        tasks = []
        if self._ozon:
            tasks.append(self._resolve_ozon(product, profile))
        else:
            profile.warnings.append("Ozon credentials are missing")
        if self._wb:
            tasks.append(self._resolve_wb(product, profile))
        else:
            profile.warnings.append("WB token is missing")
        if tasks:
            await asyncio.gather(*tasks)
        return profile

    async def _resolve_ozon(self, product: ProductInput, profile: MarketplaceProfile) -> None:
        assert self._ozon is not None
        try:
            tree = await self._cached_json("ozon-tree.json", self._ozon.category_tree)
            leaves = _flatten_ozon_tree(tree)
            best = _best_candidate(product, leaves)
            if not best:
                profile.missing_required.append(f"{product.sku}: Ozon category not found")
                return
            profile.ozon_category = CategoryMatch(
                marketplace="ozon",
                id=best.id,
                type_id=best.type_id,
                path=best.path,
                score=best.score,
            )
            if not best.type_id:
                profile.missing_required.append(f"{product.sku}: Ozon type_id not found")
                return
            attrs = await self._cached_json(
                f"ozon-attrs-{best.id}-{best.type_id}.json",
                lambda: self._ozon.category_attributes(best.id, int(best.type_id)),
            )
            profile.ozon_fields = await self._resolve_ozon_fields(product, best, attrs)
            profile.missing_required.extend(
                f"{product.sku}: Ozon {field.name} is required" for field in profile.ozon_fields if field.required and _empty(field.value)
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("ozon resolve failed sku=%s: %s", product.sku, exc)
            profile.warnings.append(f"{product.sku}: Ozon resolve failed: {str(exc)[:180]}")

    async def _resolve_wb(self, product: ProductInput, profile: MarketplaceProfile) -> None:
        assert self._wb is not None
        try:
            subjects = await self._cached_json("wb-subjects.json", self._wb.subjects)
            leaves = _flatten_wb_subjects(subjects)
            best = _best_candidate(product, leaves)
            if not best:
                profile.missing_required.append(f"{product.sku}: WB subject not found")
                return
            profile.wb_subject = CategoryMatch(marketplace="wb", id=best.id, path=best.path, score=best.score)
            charcs = await self._cached_json(
                f"wb-charcs-{best.id}.json",
                lambda: self._wb.subject_characteristics(best.id),
            )
            profile.wb_fields = await self._resolve_wb_fields(product, best, charcs)
            profile.missing_required.extend(
                f"{product.sku}: WB {field.name} is required" for field in profile.wb_fields if field.required and _empty(field.value)
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("wb resolve failed sku=%s: %s", product.sku, exc)
            profile.warnings.append(f"{product.sku}: WB resolve failed: {str(exc)[:180]}")

    async def _resolve_ozon_fields(self, product: ProductInput, category: Candidate, attrs: list[dict[str, Any]]) -> list[MarketplaceFieldValue]:
        out: list[MarketplaceFieldValue] = []
        for attr in attrs:
            attr_id = int(attr.get("id") or 0)
            if not attr_id:
                continue
            name = str(attr.get("name") or attr_id)
            required = bool(attr.get("is_required") or attr.get("required"))
            dictionary_id = int(attr.get("dictionary_id") or 0)
            if not required and not _is_useful_field(name):
                continue
            allowed: list[str] = []
            if dictionary_id:
                values = await self._cached_json(
                    f"ozon-values-{category.id}-{category.type_id}-{attr_id}.json",
                    lambda aid=attr_id: self._ozon.attribute_values(
                        attribute_id=aid,
                        category_id=category.id,
                        type_id=int(category.type_id or 0),
                    ),
                )
                allowed = _names(values, "value")
            value, source, warning = _deterministic_value(product, name, allowed, marketplace="ozon")
            out.append(
                MarketplaceFieldValue(
                    id=str(attr_id),
                    name=name,
                    required=required,
                    value=value,
                    allowed_values=allowed[:80],
                    source=source,
                    warning=warning,
                )
            )
        return out

    async def _resolve_wb_fields(self, product: ProductInput, subject: Candidate, charcs: list[dict[str, Any]]) -> list[MarketplaceFieldValue]:
        out: list[MarketplaceFieldValue] = []
        for charc in charcs:
            charc_id = int(charc.get("charcID") or charc.get("id") or 0)
            if not charc_id:
                continue
            name = str(charc.get("name") or charc_id)
            required = bool(charc.get("required") or charc.get("isRequired"))
            if not required and not _is_useful_field(name):
                continue
            allowed = _names(charc.get("values") or [], "name")
            directory_name = _wb_directory_for(name)
            if not allowed and directory_name and self._wb is not None:
                try:
                    directory_values = await self._cached_json(
                        f"wb-dir-{directory_name}.json",
                        lambda d=directory_name: self._wb.directory(d),
                    )
                    allowed = _names(directory_values, "name")
                except Exception as exc:  # noqa: BLE001
                    logger.debug("wb directory failed name=%s dir=%s: %s", name, directory_name, exc)
            value, source, warning = _deterministic_value(product, name, allowed, marketplace="wb")
            out.append(
                MarketplaceFieldValue(
                    id=str(charc_id),
                    name=name,
                    required=required,
                    value=value,
                    allowed_values=allowed[:80],
                    source=source,
                    warning=warning,
                )
            )
        return out

    async def _cached_json(self, name: str, loader) -> Any:
        path = self.cache_dir / name
        if path.exists() and time.time() - path.stat().st_mtime < self.settings.MARKETPLACE_CACHE_TTL_SEC:
            return json.loads(path.read_text(encoding="utf-8"))
        data = await loader()
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data


def _flatten_ozon_tree(tree: list[dict[str, Any]], path: str = "", parent_category_id: int | None = None) -> list[Candidate]:
    out: list[Candidate] = []
    for node in tree:
        name = str(node.get("category_name") or node.get("type_name") or "").strip()
        current_path = f"{path} / {name}" if path and name else name or path
        category_id = node.get("description_category_id") or parent_category_id
        children = node.get("children") or node.get("types") or []
        if children:
            out.extend(_flatten_ozon_tree(children, current_path, int(category_id) if category_id else parent_category_id))
            continue
        type_id = node.get("type_id")
        if category_id and type_id and current_path:
            out.append(Candidate("ozon", int(category_id), int(type_id), current_path, 0))
    return out


def _flatten_wb_subjects(subjects: list[dict[str, Any]]) -> list[Candidate]:
    out: list[Candidate] = []
    for item in subjects:
        subject_id = item.get("subjectID") or item.get("subjectId") or item.get("id")
        if not subject_id:
            continue
        subject = str(item.get("subjectName") or item.get("name") or "").strip()
        parent = str(item.get("parentName") or item.get("parent") or "").strip()
        path = f"{parent} / {subject}" if parent else subject
        out.append(Candidate("wb", int(subject_id), None, path, 0))
    return out


def _best_candidate(product: ProductInput, candidates: list[Candidate]) -> Candidate | None:
    query = " ".join([product.name, product.brand, product.extra]).strip()
    scored = sorted((_score_candidate(query, c), c) for c in candidates)
    if not scored:
        return None
    score, candidate = scored[-1]
    if score <= 0:
        return None
    return Candidate(candidate.marketplace, candidate.id, candidate.type_id, candidate.path, round(score, 4))


def _score_candidate(query: str, candidate: Candidate) -> float:
    query_tokens = _tokens(query)
    path = candidate.path.lower()
    path_tokens = _tokens(candidate.path)
    if not query_tokens or not path_tokens:
        return 0
    roots = {token[:5] for token in query_tokens if len(token) >= 4}
    score = sum(4.0 for root in roots if root in path)
    score += len(set(query_tokens) & set(path_tokens)) * 2.5
    score += SequenceMatcher(None, " ".join(query_tokens), " ".join(path_tokens)).ratio()
    return score


def _tokens(value: str) -> list[str]:
    raw = re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", value.lower())
    return [token for token in raw if len(token) > 2 and token not in STOP_WORDS and not token.isdigit()]


def _names(values: Any, key: str) -> list[str]:
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        value = item.get(key) or item.get("value") or item.get("name")
        if value is None:
            continue
        text = str(value).strip()
        if text and text not in out:
            out.append(text)
    return out


def _is_useful_field(name: str) -> bool:
    lower = name.lower()
    return any(
        word in lower
        for word in (
            "бренд",
            "страна",
            "аромат",
            "вкус",
            "цвет",
            "состав",
            "материал",
            "тип",
            "назначение",
            "объем",
            "вес",
            "размер",
            "пол",
        )
    )


def _deterministic_value(product: ProductInput, field_name: str, allowed: list[str], *, marketplace: str) -> tuple[Any, str, str]:
    lower = field_name.lower()
    source = ""
    value: Any = None
    if "бренд" in lower and product.brand:
        value, source = product.brand, "brand"
    elif "страна" in lower and ("изготов" in lower or "производ" in lower):
        value, source = "Россия", "default_country"
    elif "ндс" in lower or "vat" in lower:
        value, source = "22" if marketplace == "wb" else "0.22", "default_vat"
    elif "вес" in lower and product.weight_g:
        value, source = product.weight_g, "input_weight"
    elif ("длина" in lower or "глубина" in lower) and product.length_cm:
        value, source = product.length_cm, "input_dimension"
    elif "ширина" in lower and product.width_cm:
        value, source = product.width_cm, "input_dimension"
    elif "высота" in lower and product.height_cm:
        value, source = product.height_cm, "input_dimension"
    elif "название" in lower or "наименование" in lower:
        value, source = product.name, "input_name"
    elif "артикул" in lower:
        value, source = product.sku, "input_sku"

    if allowed:
        if value is not None:
            picked = _pick_allowed(str(value), allowed)
            if picked:
                return picked, source + ":allowed", "" if picked == str(value) else f"substituted '{value}' -> '{picked}'"
        query = " ".join([product.name, product.brand, product.extra])
        picked = _pick_allowed(query, allowed)
        if picked:
            return picked, "allowed_match", ""
        return None, "", f"no allowed value matched for {field_name}"
    return value, source, ""


def _pick_allowed(query: str, allowed: list[str]) -> str | None:
    query_l = query.lower()
    best: tuple[float, str] | None = None
    for item in allowed:
        item_l = item.lower()
        if item_l and item_l in query_l:
            score = 10 + len(item_l) / 100
        else:
            score = SequenceMatcher(None, query_l, item_l).ratio()
        if best is None or score > best[0]:
            best = (score, item)
    if not best:
        return None
    return best[1] if best[0] >= 0.58 else None


def _wb_directory_for(name: str) -> str | None:
    lower = name.lower()
    if "цвет" in lower:
        return "colors"
    if "страна" in lower:
        return "countries"
    if "сезон" in lower:
        return "seasons"
    if "пол" in lower:
        return "kinds"
    if "ндс" in lower:
        return "vat"
    return None


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == []
