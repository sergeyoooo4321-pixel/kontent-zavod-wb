"""Internal API для гнома (cz-gnome.service на :8001).

Эндпоинты под защитой X-Internal-Token. Гном дёргает их HTTP'ом, чтобы
переиспользовать уже поднятые в cz-backend клиенты (kie, s3, wb, ozon)
без второго экземпляра конфигов и сетевых сессий.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from .config import settings
from .pipeline import Deps
from .prompts import (
    build_design_director_system,
    build_design_director_user,
    compile_image_prompt,
    build_extra_prompt,
    build_main_prompt,
    build_pack_prompt,
)
from .s3 import S3Client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/internal", tags=["internal"])


def _check_token(token: str | None) -> None:
    if settings.INTERNAL_TOKEN and token != settings.INTERNAL_TOKEN:
        raise HTTPException(status_code=403, detail="bad internal token")


# ─── /internal/generate_image ──────────────────────────────────────


class GenImageIn(BaseModel):
    src_url: str  # публичный URL исходной фотки (юзер прислал, уже в S3)
    brand: str
    name: str
    sku: str = ""  # опционально для tagging в S3
    variants: list[str] = ["main", "pack2", "pack3", "extra"]


class GenImageOut(BaseModel):
    ok: bool
    images: dict[str, str]  # tag → public_url
    errors: dict[str, str]  # tag → error msg
    brief: dict | None = None


@router.post("/generate_image", response_model=GenImageOut)
async def gen_image(
    req: GenImageIn,
    request: Request,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
):
    _check_token(x_internal_token)
    deps: Deps = request.app.state.deps
    sku = req.sku or uuid.uuid4().hex[:8]
    batch_id = f"gnome-{int(time.time())}-{uuid.uuid4().hex[:6]}"

    # 1. brief через vision LLM (как в pipeline.process_product_images)
    brief: dict = {}
    try:
        brief = await deps.kie.chat_json_with_vision(
            system=build_design_director_system(),
            user=build_design_director_user(req.name, req.brand),
            image_url=req.src_url,
        )
        logger.info("internal/gen_image brief sku=%s ok", sku)
    except Exception as e:
        logger.warning("internal/gen_image brief failed sku=%s: %s — generic", sku, e)
        brief = {}

    # 2. Параллельно все варианты
    import asyncio
    images: dict[str, str] = {}
    errors: dict[str, str] = {}

    async def _gen_one(tag: str) -> tuple[str, str | None, str | None]:
        try:
            prompt = compile_image_prompt(brief, req.name, mode=tag if tag in ("main", "extra") else "pack")
            kie_url = await deps.kie.generate_image_with_retry(
                prompt=prompt,
                input_urls=[req.src_url],
            )
            content = await deps.s3.fetch(kie_url)
            public = await deps.s3.put_public(
                S3Client.build_key(batch_id, sku, tag), content,
            )
            return tag, public, None
        except Exception as e:
            return tag, None, str(e)[:300]

    results = await asyncio.gather(*[_gen_one(t) for t in req.variants])
    for tag, url, err in results:
        if url:
            images[tag] = url
        else:
            errors[tag] = err or "unknown"

    return GenImageOut(
        ok=bool(images),
        images=images,
        errors=errors,
        brief=brief or None,
    )


# ─── /internal/match_category ──────────────────────────────────────


class MatchCategoryIn(BaseModel):
    name: str
    brand: str = ""
    main_image_url: str | None = None
    side: str = "both"  # "ozon" | "wb" | "both"


class MatchCategoryOut(BaseModel):
    ok: bool
    ozon: list[dict] | None = None
    wb: list[dict] | None = None
    error: str | None = None


@router.post("/match_category", response_model=MatchCategoryOut)
async def match_category(
    req: MatchCategoryIn,
    request: Request,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
):
    """Простейший категори-матчинг: возвращает топ кандидатов по справочникам.

    На этом MVP-этапе — просто LLM на name+brand, без полного pipeline.
    Если нужен глубокий поиск с справочниками — гном может попросить юзера
    запустить старый кнопочный сценарий «📦 Новая партия».
    """
    _check_token(x_internal_token)
    deps: Deps = request.app.state.deps
    out: dict[str, Any] = {"ok": True}
    try:
        from .pipeline import _llm_pick_category_top  # type: ignore  # may not exist
    except ImportError:
        _llm_pick_category_top = None  # type: ignore

    # Фолбэк: если хелпера нет, возвращаем пустые кандидаты с пометкой.
    out["ozon"] = None
    out["wb"] = None
    out["error"] = "match_category на этом MVP-этапе не реализовал полный пайплайн — используй кнопку «📦 Новая партия» в боте"
    return MatchCategoryOut(**out)


# ─── /internal/fill_card ───────────────────────────────────────────


class FillCardIn(BaseModel):
    sku: str
    brand: str
    name: str
    images: dict[str, str]  # tag → public_url
    cabinet: str | None = None  # имя кабинета или None = default
    dry_run: bool = True


class FillCardOut(BaseModel):
    ok: bool
    dry_run: bool
    payload: dict | None = None
    error: str | None = None


@router.post("/fill_card", response_model=FillCardOut)
async def fill_card(
    req: FillCardIn,
    request: Request,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
):
    """MVP: собирает базовый payload карточки. Полная заливка с атрибутами —
    через старый кнопочный pipeline (где есть категории, справочники, валидация).
    """
    _check_token(x_internal_token)
    payload = {
        "sku": req.sku,
        "brand": req.brand,
        "name": req.name,
        "images": req.images,
        "cabinet": req.cabinet or settings.default_cabinet_name,
        "note": "MVP-payload от гнома. Полная заливка — через «📦 Новая партия».",
    }
    if req.dry_run:
        return FillCardOut(ok=True, dry_run=True, payload=payload)
    return FillCardOut(
        ok=False,
        dry_run=False,
        error="Полная заливка через гнома пока не реализована. "
              "Используй DRY_RUN=true и/или старый кнопочный сценарий.",
    )


# ─── /internal/parse_template ──────────────────────────────────────


class ParseTemplateIn(BaseModel):
    xlsx_path: str                      # абсолютный путь к xlsx-файлу на сервере
    cabinet: str | None = None          # имя кабинета или "default"
    save_as: str | None = None          # имя для сохранения (без расширения)


class ParseTemplateOut(BaseModel):
    ok: bool
    saved_to: str | None = None
    marketplace: str | None = None
    sheet_name: str | None = None
    n_fields: int = 0
    n_required: int = 0
    n_with_dropdown: int = 0
    category_id: int | None = None
    type_id: int | None = None
    parse_warnings: list[str] = []
    error: str | None = None


@router.post("/parse_template", response_model=ParseTemplateOut)
async def parse_template_endpoint(
    req: ParseTemplateIn,
    request: Request,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
):
    """Парсит xlsx-шаблон Ozon/WB через app.excel.parser, сохраняет JSON-структуру
    в ~/cz-backend/templates/<cabinet>/<marketplace>_<save_as>.json.

    Используется скиллом гнома `parse_template` как первый шаг Excel-флоу.
    """
    _check_token(x_internal_token)
    import dataclasses
    import json
    from pathlib import Path

    from .excel.parser import parse_template as _parse

    p = Path(req.xlsx_path).expanduser()
    if not p.exists():
        return ParseTemplateOut(ok=False, error=f"файл не найден: {req.xlsx_path}")
    if p.suffix.lower() != ".xlsx":
        return ParseTemplateOut(ok=False, error=f"не xlsx: {p.suffix}")

    try:
        spec = _parse(p)
    except Exception as e:
        logger.exception("parse_template fail %s", req.xlsx_path)
        return ParseTemplateOut(ok=False, error=f"парсинг упал: {str(e)[:300]}")

    cabinet = (req.cabinet or "default").strip() or "default"
    save_name = (req.save_as or p.stem).strip() or p.stem
    out_dir = Path.home() / "cz-backend" / "templates" / cabinet
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{spec.marketplace}_{save_name}.json"

    try:
        out_path.write_text(
            json.dumps(dataclasses.asdict(spec), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        return ParseTemplateOut(ok=False, error=f"сохранение упало: {str(e)[:200]}")

    n_required = sum(1 for f in spec.fields if f.required)
    n_with_dd = sum(1 for f in spec.fields if f.dropdown)
    return ParseTemplateOut(
        ok=True,
        saved_to=str(out_path),
        marketplace=spec.marketplace,
        sheet_name=spec.sheet_name,
        n_fields=len(spec.fields),
        n_required=n_required,
        n_with_dropdown=n_with_dd,
        category_id=spec.category_id,
        type_id=spec.type_id,
        parse_warnings=spec.parse_warnings or [],
    )
