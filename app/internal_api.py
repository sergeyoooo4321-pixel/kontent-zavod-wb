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


# ─── /internal/fill_excel_batch ────────────────────────────────────


class ProductInput(BaseModel):
    sku: str
    name: str
    brand: str = ""
    weight_g: int | None = None              # вес одиночки в граммах
    dims: dict[str, float] | None = None     # {l,w,h} см
    images: dict[str, str] = {}              # tag → URL


class PendingQuestion(BaseModel):
    field_id: str                            # уникальный для answers (sku::field)
    field_name: str
    sku: str
    title: str
    options: list[dict] = []                 # {label, score?, id?, detail?}
    allow_freetext: bool = True


class FillExcelBatchIn(BaseModel):
    template_json_path: str
    products: list[ProductInput]
    cabinet: str | None = None
    answers: dict[str, str] = {}             # field_id → answer
    output_filename: str | None = None       # имя сохраняемого xlsx


class FillExcelBatchOut(BaseModel):
    ok: bool
    state: str                               # "filled" | "pending" | "error"
    xlsx_path: str | None = None
    pending: list[PendingQuestion] = []
    skus_total: int = 0
    skus_filled: int = 0
    error: str | None = None


_FIELD_NAME_MAPPINGS = {
    # Ключевое слово в имени поля → ключ-резолвер (lower-case)
    "артикул": "sku",
    "vendorcode": "sku",
    "название товара": "title",
    "наименование": "title_wb_full",
    "бренд": "brand",
    "описание": "description",
    "аннотация": "description",
    "категория продавца": "category_path",
    "вес товара": "weight_unit_g",
    "вес в упаковке": "weight_packed_g",
    "вес брутто": "weight_packed_g",
    "длина": "dim_l",
    "ширина": "dim_w",
    "высота": "dim_h",
    "глубина": "dim_l",
    "ндс": "vat",
    "цена, руб": "price",
    "цена до скидки": "price_old",
    "штрих": "barcode_skip",
    "артикул wb": "wb_article_skip",
    "группа": "group_name",
    "фото": "image_main",
}


def _resolve_field_key(field_name: str) -> str | None:
    """По имени поля шаблона определяет универсальный ключ-резолвер.
    Возвращает None если поле «нестандартное» (атрибут категории).
    """
    nm = (field_name or "").lower()
    for kw, key in _FIELD_NAME_MAPPINGS.items():
        if kw in nm:
            return key
    return None


def _det_value_for_sku(
    field_key: str,
    sku_row: dict[str, Any],
    parsed: dict[str, str],
    product: ProductInput,
    marketplace: str,
    category_path: str,
) -> str | None:
    """Детерминированное значение для известного field_key. None = пропустить."""
    from .normalize import format_ozon_title, format_wb_full_title, format_wb_short_title
    from .rules import nds_value

    qty = sku_row.get("qty", 1)
    dims = sku_row.get("dims") or {}
    if field_key == "sku":
        return sku_row["sku"]
    if field_key == "title":
        if marketplace == "ozon":
            return format_ozon_title(parsed, qty=qty)
        return format_wb_full_title(parsed, qty=qty)
    if field_key == "title_wb_full":
        if marketplace == "wb":
            return format_wb_full_title(parsed, qty=qty)
        return format_ozon_title(parsed, qty=qty)
    if field_key == "title_wb_short":
        return format_wb_short_title(parsed)
    if field_key == "brand":
        return product.brand or parsed.get("brand", "")
    if field_key == "description":
        # placeholder — реальная аннотация генерится LLM (этап 3 батчевый),
        # здесь пишем хотя бы имя товара чтобы поле не было пустым.
        return product.name
    if field_key == "category_path":
        return category_path
    if field_key == "weight_unit_g":
        return str(sku_row.get("weight_unit_g") or 0) or None
    if field_key == "weight_packed_g":
        v = sku_row.get("weight_packed_g") or 0
        if marketplace == "wb":
            # WB ожидает кг с 2 знаками
            return str(sku_row.get("weight_wb_kg") or 0)
        return str(v) if v else None
    if field_key == "dim_l":
        return str(dims.get("l", 0)) or None
    if field_key == "dim_w":
        return str(dims.get("w", 0)) or None
    if field_key == "dim_h":
        return str(dims.get("h", 0)) or None
    if field_key == "vat":
        return str(nds_value())  # 22
    if field_key == "price":
        return "0"
    if field_key == "price_old":
        return ""
    if field_key in ("barcode_skip", "wb_article_skip"):
        return ""
    if field_key == "group_name":
        # WB-группа: bren_subjectId или просто бренд
        brand = product.brand or parsed.get("brand", "")
        return brand or product.sku
    if field_key == "image_main":
        return product.images.get("main", "")
    return None


async def _resolve_category_path(spec, deps: Deps) -> str:
    """Возвращает читаемый путь категории для поля «Категория продавца».

    Приоритет:
      1. Ozon + есть category_id → ищем в дереве категорий (с кешем).
      2. Имя файла шаблона без расширения (например «Стиральные порошки»).
      3. Пусто.
    """
    from pathlib import Path

    if spec.marketplace == "ozon" and spec.category_id and deps.ozon is not None:
        try:
            tree = await deps.ozon.category_tree()
            path = _find_ozon_path_by_id(tree, int(spec.category_id))
            if path:
                return path
        except Exception as e:
            logger.warning("ozon category tree resolve failed: %s", e)

    raw = getattr(spec, "raw_path", "") or ""
    if raw:
        return Path(raw).stem.strip()
    return ""


def _find_ozon_path_by_id(tree: list[dict], target_id: int, prefix: str = "") -> str:
    """Рекурсивный поиск Ozon-категории по description_category_id."""
    for n in tree:
        name = n.get("category_name") or n.get("type_name") or ""
        cur = f"{prefix} / {name}" if prefix else name
        cat_id = n.get("description_category_id")
        if cat_id and int(cat_id) == target_id:
            return cur
        children = n.get("children") or n.get("types") or []
        if children:
            found = _find_ozon_path_by_id(children, target_id, cur)
            if found:
                return found
    return ""


@router.post("/fill_excel_batch", response_model=FillExcelBatchOut)
async def fill_excel_batch_endpoint(
    req: FillExcelBatchIn,
    request: Request,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
):
    """Заполняет xlsx-шаблон по 3 SKU на товар через правила §5.2.

    Двухфазный flow:
      1. Первый вызов (answers пуст): прогоняет детерминированно, на required-полях
         с dropdown'ом возвращает pending-вопросы.
      2. Второй вызов (answers заполнен): применяет ответы, пишет xlsx, возвращает путь.
    """
    _check_token(x_internal_token)
    import dataclasses
    import json
    from pathlib import Path

    from openpyxl import load_workbook

    from .excel.parser import TemplateField, TemplateSpec
    from .normalize import parse_input_line
    from .rules import expand_to_3_skus

    # 1. Восстанавливаем TemplateSpec из JSON
    json_p = Path(req.template_json_path).expanduser()
    if not json_p.exists():
        return FillExcelBatchOut(ok=False, state="error",
                                 error=f"template json не найден: {json_p}")
    try:
        spec_dict = json.loads(json_p.read_text(encoding="utf-8"))
        fields = [TemplateField(**f) for f in spec_dict.get("fields") or []]
        spec_dict["fields"] = fields
        spec = TemplateSpec(**spec_dict)
    except Exception as e:
        return FillExcelBatchOut(ok=False, state="error",
                                 error=f"json parse failed: {str(e)[:200]}")

    if not spec.raw_path or not Path(spec.raw_path).exists():
        return FillExcelBatchOut(
            ok=False, state="error",
            error=f"оригинал xlsx не найден: {spec.raw_path}",
        )

    # 2. Разворачиваем продукты на 3 SKU + парсим имена
    sku_rows: list[dict[str, Any]] = []   # развёрнутые SKU с их продуктом
    for product in req.products:
        parsed = parse_input_line(product.name, brand_hint=product.brand)
        rows = expand_to_3_skus({
            "sku": product.sku,
            "name": product.name,
            "weight": product.weight_g or 0,
            "dims": product.dims or {"l": 15, "w": 10, "h": 5},
        }, dims_from_internet=True)
        for r in rows:
            r["_product"] = product
            r["_parsed"] = parsed
            sku_rows.append(r)

    if not sku_rows:
        return FillExcelBatchOut(ok=False, state="error", error="продуктов нет")

    # 3. Резолвим category_path по category_id (Ozon) или из имени файла шаблона
    deps: Deps = request.app.state.deps
    category_path = await _resolve_category_path(spec, deps)

    # 3b. Подтягиваем cached answers per-product (по бренду+имени)
    from .decision_cache import append_cache, read_cached_answers
    cabinet_norm = (req.cabinet or "default").strip() or "default"
    cached_per_sku: dict[str, dict[str, str]] = {}
    for product in req.products:
        cached = read_cached_answers(
            cabinet_norm, spec.marketplace, product.brand, product.name,
        )
        # Распространяем cached на все 3 SKU этого product
        for sku_row in sku_rows:
            if sku_row["_product"].sku == product.sku:
                cached_per_sku[sku_row["sku"]] = cached
    pending: list[PendingQuestion] = []
    filled_rows: list[dict[int, str]] = []  # для каждого SKU: column → value

    for sku_row in sku_rows:
        product: ProductInput = sku_row["_product"]
        parsed = sku_row["_parsed"]
        row_values: dict[int, str] = {}
        cached = cached_per_sku.get(sku_row["sku"], {})
        for field in spec.fields:
            field_key = _resolve_field_key(field.name)
            value: str | None = None
            answer_key = f"{sku_row['sku']}::{field.name}"

            # 3a. Сначала проверяем явный answer от юзера
            if answer_key in req.answers:
                ans = req.answers[answer_key].strip()
                if ans:
                    value = ans

            # 3b. Иначе — детерминированно
            if value is None and field_key:
                value = _det_value_for_sku(
                    field_key, sku_row, parsed, product,
                    spec.marketplace, category_path,
                )

            # 3b'. Иначе — из persistent cache decisions.jsonl
            if (value is None or value == "") and field.name in cached:
                value = cached[field.name]

            # 3c. Если поле required и до сих пор пусто — pending
            if (value is None or value == "") and field.required:
                # Подготовим options для ask_user_choice
                options: list[dict] = []
                if field.dropdown:
                    for v in field.dropdown[:5]:
                        options.append({"label": v})
                pending.append(PendingQuestion(
                    field_id=answer_key,
                    field_name=field.name,
                    sku=sku_row["sku"],
                    title=f"{sku_row['sku']} — поле «{field.name}»: какое значение?",
                    options=options,
                    allow_freetext=True,
                ))
                continue

            # 3d. Не required и не нашли — пропускаем (xlsx останется пустым)
            if value is None or value == "":
                continue

            row_values[field.column] = value
        filled_rows.append(row_values)

    # 4. Если есть pending — возвращаем (без записи xlsx)
    if pending:
        return FillExcelBatchOut(
            ok=True, state="pending",
            pending=pending,
            skus_total=len(sku_rows),
            skus_filled=0,
        )

    # 5. Все required заполнены — пишем xlsx
    try:
        wb = load_workbook(spec.raw_path, data_only=False)
        ws = wb[spec.sheet_name] if spec.sheet_name in wb.sheetnames else wb.active

        for i, row_values in enumerate(filled_rows):
            target_row = spec.data_start_row + i
            for col, val in row_values.items():
                ws.cell(row=target_row, column=col, value=val)

        cabinet = (req.cabinet or "default").strip() or "default"
        out_dir = Path.home() / "cz-backend" / "filled" / cabinet
        out_dir.mkdir(parents=True, exist_ok=True)
        fname = req.output_filename or f"{Path(spec.raw_path).stem}_filled.xlsx"
        out_path = out_dir / fname
        wb.save(out_path)
    except Exception as e:
        logger.exception("fill_excel_batch save failed")
        return FillExcelBatchOut(ok=False, state="error",
                                 error=f"save failed: {str(e)[:200]}")

    # 6. Записываем в persistent cache те answers что юзер дал в этой партии
    #    (только настоящие user-answers, не наши авто-fill).
    for product in req.products:
        # собираем по всем 3 SKU этого продукта
        merged: dict[str, str] = {}
        for sku_row in sku_rows:
            if sku_row["_product"].sku != product.sku:
                continue
            sku = sku_row["sku"]
            prefix = f"{sku}::"
            for k, v in (req.answers or {}).items():
                if k.startswith(prefix) and v:
                    field_name = k[len(prefix):]
                    merged[field_name] = v
        if merged:
            try:
                append_cache(cabinet_norm, spec.marketplace,
                             product.brand, product.name, merged)
            except Exception as e:
                logger.warning("cache append fail %s: %s", product.sku, e)

    return FillExcelBatchOut(
        ok=True, state="filled",
        xlsx_path=str(out_path),
        skus_total=len(sku_rows),
        skus_filled=len(sku_rows),
    )


# ─── /internal/deliver_excel ───────────────────────────────────────


class DeliverExcelIn(BaseModel):
    xlsx_path: str
    chat_id: int
    caption: str | None = None
    filename: str | None = None      # переопределить имя при отправке


class DeliverExcelOut(BaseModel):
    ok: bool
    sent_filename: str | None = None
    size_bytes: int = 0
    error: str | None = None


@router.post("/deliver_excel", response_model=DeliverExcelOut)
async def deliver_excel_endpoint(
    req: DeliverExcelIn,
    request: Request,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
):
    """Отправляет готовый xlsx юзеру в Telegram через TelegramClient.send_document."""
    _check_token(x_internal_token)
    from pathlib import Path

    p = Path(req.xlsx_path).expanduser()
    if not p.exists():
        return DeliverExcelOut(ok=False, error=f"файл не найден: {req.xlsx_path}")
    if p.suffix.lower() != ".xlsx":
        return DeliverExcelOut(ok=False, error=f"не xlsx: {p.suffix}")

    try:
        content = p.read_bytes()
    except Exception as e:
        return DeliverExcelOut(ok=False, error=f"чтение упало: {str(e)[:200]}")

    fname = req.filename or p.name
    deps: Deps = request.app.state.deps
    try:
        await deps.tg.send_document(
            chat_id=req.chat_id,
            content=content,
            filename=fname,
            caption=req.caption or f"📋 *{fname}*",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.exception("deliver_excel TG send failed")
        return DeliverExcelOut(ok=False, error=f"telegram: {str(e)[:200]}")

    return DeliverExcelOut(ok=True, sent_filename=fname, size_bytes=len(content))
