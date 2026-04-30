"""Оркестратор пайплайна обработки партии товаров.

Этапы:
1. фото из Telegram → S3 (исходное)
2. kie.ai генерация 4 фото на товар (main с ref=src; pack2/pack3/extra с ref=main)
3. подбор категории Ozon+WB через LLM
4. скачивание шаблона и справочников
5. расширение до 3 SKU + LLM-тексты + маппинг полей
6. заливка через Ozon/WB API
7. финальный Markdown-отчёт
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from .config import settings
from .kie_ai import KieAIClient, KieAIError, KieAITimeout
from .models import (
    CategoryRef,
    ProductState,
    Report,
    ReportItem,
    RunRequest,
)
from .mapping import map_ozon_attributes, map_wb_characteristics
from .ozon import OzonClient, OzonError
from .prompts import (
    build_attributes_prompts,
    build_category_prompts,
    build_characteristics_prompts,
    build_design_director_system,
    build_design_director_user,
    build_extra_prompt,
    build_main_prompt,
    build_pack_prompt,
    build_titles_prompts,
    compile_image_prompt,
)
from .reports import build_final_report_md
from .rules import expand_to_3_skus, join_multivalue, limit_chars, nds_value, pick_from_dict, strip_brand
from .s3 import S3Client, S3Error
from .telegram import TelegramClient
from .wb import WBClient, WBError

logger = logging.getLogger(__name__)


@dataclass
class Deps:
    tg: TelegramClient
    kie: KieAIClient
    s3: S3Client
    ozon: OzonClient
    wb: WBClient


# ─── Этап 1: фото ────────────────────────────────────────────────


async def process_product_images(
    state: ProductState,
    batch_id: str,
    chat_id: int,
    deps: Deps,
    sem: asyncio.Semaphore,
) -> None:
    """Non-cascading parallel pipeline:
        src → identity+design (1 vision LLM call) → ВСЕ 4 параллельно с ref=src

    КРИТИЧНО: ни одна генерация не использует другую сгенерированную картинку
    как reference. Только оригинал юзера. Identity-Lock в каждом промпте
    защищает упаковку от изменений.
    """
    async with sem:
        # 1. скачать из TG → S3 (src) — это якорь идентичности на ВСЕ генерации
        try:
            raw = await deps.tg.get_file_bytes(state.tg_file_id)
            src_key = S3Client.build_key(batch_id, state.sku, "src")
            state.src_url = await deps.s3.put_public(src_key, raw, "image/jpeg")
        except Exception as e:
            from .telegram import _mask_token
            msg = _mask_token(str(e))
            state.errors.append(f"src: {msg}")
            logger.error("src upload %s: %s", state.sku, msg)
            return

        try:
            await deps.tg.send(
                chat_id,
                f"📥 `{state.sku}`: фото в S3 → анализирую идентичность товара…",
                parse_mode="Markdown",
            )
        except Exception:
            pass

        # 2. ОДИН vision-вызов: identity + design в одном JSON
        brief: dict = {}
        try:
            brief = await deps.kie.chat_json_with_vision(
                system=build_design_director_system(),
                user=build_design_director_user(state.name, state.brand),
                image_url=state.src_url,
            )
            identity = brief.get("identity") or {}
            design = brief.get("design") or {}
            logger.info(
                "brief %s: shape=%s, scene=%s",
                state.sku,
                (identity.get("shape") or "")[:60],
                (design.get("scene") or "")[:60],
            )
            try:
                vibe = (design.get("overall_vibe") or design.get("scene") or "")[:200]
                shape = (identity.get("shape") or "")[:120]
                await deps.tg.send(
                    chat_id,
                    f"🎨 `{state.sku}`:\n"
                    f"• identity: {shape}\n"
                    f"• design: {vibe}\n"
                    f"Генерю 4 фото параллельно от оригинала…",
                    parse_mode="Markdown",
                )
            except Exception:
                pass
        except Exception as e:
            logger.warning("brief %s failed, fallback to generic: %s", state.sku, e)
            brief = {}  # fallback

        # 3. ВСЕ 4 генерации параллельно, ВСЕ используют ОРИГИНАЛ как ref.
        # Никакой каскадной зависимости main → pack/extra.
        async def _gen(tag: str, prompt: str) -> tuple[str, str | None, str | None]:
            try:
                kie_url = await deps.kie.generate_image(
                    prompt=prompt,
                    input_urls=[state.src_url],  # ВСЕГДА оригинал
                )
                content = await deps.s3.fetch(kie_url)
                public = await deps.s3.put_public(
                    S3Client.build_key(batch_id, state.sku, tag), content
                )
                return tag, public, None
            except (KieAIError, KieAITimeout, S3Error) as e:
                return tag, None, str(e)
            except Exception as e:
                logger.exception("unexpected %s/%s: %s", state.sku, tag, e)
                return tag, None, f"unexpected: {e}"

        main_prompt = (
            compile_image_prompt(brief, state.name, mode="main")
            if brief else build_main_prompt(state.name, state.brand)
        )
        pack2_prompt = (
            compile_image_prompt(brief, state.name, mode="pack2", qty=2)
            if brief else build_pack_prompt(state.name, 2)
        )
        pack3_prompt = (
            compile_image_prompt(brief, state.name, mode="pack3", qty=3)
            if brief else build_pack_prompt(state.name, 3)
        )
        extra_prompt = (
            compile_image_prompt(brief, state.name, mode="extra")
            if brief else build_extra_prompt(state.name)
        )
        results = await asyncio.gather(
            _gen("main", main_prompt),
            _gen("pack2", pack2_prompt),
            _gen("pack3", pack3_prompt),
            _gen("extra", extra_prompt),
        )
        for tag, url, err in results:
            if url:
                state.images[tag] = url
            else:
                state.errors.append(f"{tag}: {err}")

        # 4. Отдаём пользователю: альбом + ссылки + ZIP
        try:
            await _send_product_results(state, chat_id, deps)
        except Exception as e:
            logger.warning("_send_product_results %s: %s", state.sku, e)


async def _send_product_results(state: ProductState, chat_id: int, deps: Deps) -> None:
    """Отправить юзеру альбом из 4 фото, текстовый список URL и ZIP-архив."""
    tags_order = [("main", "Главное"), ("pack2", "Набор 2 шт"),
                  ("pack3", "Набор 3 шт"), ("extra", "Доп. фото")]

    # 1. альбом
    photos = []
    for tag, label in tags_order:
        url = state.images.get(tag)
        if url:
            photos.append((url, None))
    if photos:
        first_url, _ = photos[0]
        photos[0] = (first_url, f"🖼 *{state.sku}* — {len(photos)}/4 фото")
        try:
            await deps.tg.send_media_group(chat_id, photos)
        except Exception as e:
            logger.warning("media_group fail %s: %s", state.sku, e)

    # 2. список ссылок (URL в `backticks` чтобы Markdown не ломал underscore'ы)
    lines = [f"📎 *{state.sku}* — ссылки:"]
    for tag, label in tags_order:
        url = state.images.get(tag)
        if url:
            lines.append(f"• {label}: `{url}`")
    try:
        await deps.tg.send(chat_id, "\n".join(lines), parse_mode="Markdown")
    except Exception:
        # Фолбэк без Markdown — просто текст
        try:
            plain = "\n".join(f"{l.replace('*','').replace('`','')}" for l in lines)
            await deps.tg.send(chat_id, plain, parse_mode=None)
        except Exception:
            pass

    # 3. ZIP-архив со всеми фото
    try:
        zip_bytes = await _build_zip(state, deps)
        if zip_bytes:
            caption = f"📦 *{state.sku}* — `{state.name}`\n4 фото в архиве"
            await deps.tg.send_document(
                chat_id, zip_bytes, f"{state.sku}.zip",
                caption=caption, parse_mode="Markdown",
            )
    except Exception as e:
        logger.warning("zip send fail %s: %s", state.sku, e)


async def _build_zip(state: ProductState, deps: Deps) -> bytes:
    """Скачать все 4 фото и упаковать в ZIP в памяти."""
    import io
    import zipfile

    tags = ["main", "pack2", "pack3", "extra", "src"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for tag in tags:
            url = state.images.get(tag) if tag != "src" else state.src_url
            if not url:
                continue
            try:
                data = await deps.s3.fetch(url)
                zf.writestr(f"{state.sku}_{tag}.jpg", data)
            except Exception as e:
                logger.warning("zip fetch %s/%s: %s", state.sku, tag, e)
    buf.seek(0)
    return buf.getvalue()


# ─── Этап 2: категории ───────────────────────────────────────────


def _flatten_tree(tree: list[dict], path: str = "", is_ozon: bool = True) -> list[dict]:
    """Сжимает дерево категорий в плоский список листьев."""
    out: list[dict] = []
    for n in tree:
        if is_ozon:
            name = n.get("category_name") or n.get("type_name") or ""
            children = n.get("children") or n.get("types") or []
        else:
            name = n.get("subjectName") or n.get("parentName") or ""
            children = n.get("childs") or n.get("children") or []
        cur_path = f"{path} / {name}" if path else name
        if children:
            out.extend(_flatten_tree(children, cur_path, is_ozon))
        else:
            if is_ozon:
                out.append({
                    "id": n.get("description_category_id") or n.get("type_id"),
                    "type_id": n.get("type_id"),
                    "path": cur_path,
                })
            else:
                out.append({
                    "id": n.get("subjectID") or n.get("subjectId"),
                    "path": cur_path,
                })
    return out


async def match_category(state: ProductState, ozon_leaves: list[dict], wb_leaves: list[dict], deps: Deps) -> None:
    """Подбор категории через LLM. Если упало — оставляем None и в errors."""
    try:
        system, user = build_category_prompts(state.name, ozon_leaves, wb_leaves)
        resp = await deps.kie.chat_json(system=system, user=user, temperature=settings.LLM_TEMPERATURE)
        ozon_id = int(resp.get("ozon_id") or 0)
        ozon_type_id = int(resp.get("ozon_type_id") or 0)
        wb_id = int(resp.get("wb_id") or 0)
        score = float(resp.get("score") or 0.5)
        if ozon_id:
            o = next((x for x in ozon_leaves if x["id"] == ozon_id), None)
            state.ozon_category = CategoryRef(
                id=ozon_id, type_id=ozon_type_id or None,
                path=(o or {}).get("path", ""), score=score,
            )
        if wb_id:
            w = next((x for x in wb_leaves if x["id"] == wb_id), None)
            state.wb_subject = CategoryRef(
                id=wb_id, path=(w or {}).get("path", ""), score=score,
            )
    except Exception as e:
        state.errors.append(f"category: {e}")
        logger.exception("match_category %s: %s", state.sku, e)


# ─── Этап 3: справочники + шаблоны ───────────────────────────────


@dataclass
class CategoryData:
    ozon_attrs: list[dict]
    ozon_attr_values: dict[int, list[dict]]  # attribute_id → values list
    wb_charcs: list[dict]
    wb_charc_values: dict[int, list[dict]]   # charcID → values list


def dedup_categories(states: list[ProductState]) -> set[tuple[int, int | None, int]]:
    """Возвращает уникальные пары (ozon_id, ozon_type_id, wb_id) для дедупа."""
    out: set[tuple[int, int | None, int]] = set()
    for s in states:
        if s.ozon_category and s.wb_subject:
            out.add((s.ozon_category.id, s.ozon_category.type_id, s.wb_subject.id))
    return out


async def load_category_data(
    ozon_id: int, ozon_type_id: int | None, wb_id: int, deps: Deps
) -> CategoryData:
    ozon_attrs = []
    ozon_vals: dict[int, list[dict]] = {}
    if settings.has_ozon_creds and ozon_type_id:
        ozon_attrs = await deps.ozon.category_attributes(ozon_id, ozon_type_id)
        # V8 — параллельная подгрузка значений для всех атрибутов с dictionary_id
        attrs_with_dict = [a for a in ozon_attrs if a.get("dictionary_id")]

        async def _values(a):
            try:
                vals = await deps.ozon.attribute_values(a["id"], ozon_id, ozon_type_id)
                return a["id"], vals
            except OzonError as e:
                logger.warning("attribute_values %s err: %s", a["id"], e)
                return a["id"], []

        if attrs_with_dict:
            for aid, vals in await asyncio.gather(*[_values(a) for a in attrs_with_dict]):
                ozon_vals[aid] = vals

    wb_charcs = []
    wb_vals: dict[int, list[dict]] = {}
    if settings.has_wb_creds:
        wb_charcs = await deps.wb.subject_charcs(wb_id)
        for c in wb_charcs:
            dname = c.get("dictionary") or c.get("source")
            if dname:
                try:
                    charc_id = c.get("charcID") or c.get("id")
                    wb_vals[int(charc_id)] = await deps.wb.directory_values(dname)
                except WBError as e:
                    logger.warning("directory_values %s err: %s", dname, e)

    return CategoryData(ozon_attrs, ozon_vals, wb_charcs, wb_vals)


# ─── Этап 4: тексты + маппинг + расширение SKU ───────────────────


async def build_skus_and_texts(
    state: ProductState,
    cat_data: dict[tuple, CategoryData],
    deps: Deps,
) -> None:
    if not state.ozon_category or not state.wb_subject:
        state.errors.append("titles: no category")
        return

    # 1. расширение до 3 SKU (C7 — реальные размеры/вес если переданы юзером)
    # Если weight/dims не заданы — LLM-fallback по названию (TODO), пока дефолт.
    weight = getattr(state, "_weight_g", 100) or 100  # 100г по умолчанию
    dims = getattr(state, "_dims", None) or {"l": 15, "w": 10, "h": 5}  # дефолт ~среднее
    state.skus_3 = expand_to_3_skus(
        {"sku": state.sku, "name": state.name, "weight": weight, "dims": dims},
        dims_from_internet=True,  # +1 см подстраховка
    )

    cat_key = (state.ozon_category.id, state.ozon_category.type_id, state.wb_subject.id)
    cat = cat_data.get(cat_key)

    # 2. LLM тексты + атрибуты + характеристики по каждой qty
    for sku_row in state.skus_3:
        sku = sku_row["sku"]
        try:
            system, user = build_titles_prompts(
                state.name,
                state.brand,
                state.ozon_category.path,
                state.wb_subject.path,
                sku_row["qty"],
            )
            txt = await deps.kie.chat_json(system=system, user=user)
            state.titles[sku] = {
                "title_ozon": limit_chars(txt.get("title_ozon", ""), 200),
                "title_wb_short": limit_chars(strip_brand(txt.get("title_wb_short", ""), state.brand), 60),
                "title_wb_full": limit_chars(txt.get("title_wb_full", ""), 60),
                "annotation_ozon": txt.get("annotation_ozon", ""),
                "composition_wb": limit_chars(txt.get("composition_wb", ""), 100),
            }
        except Exception as e:
            state.errors.append(f"titles {sku}: {e}")
            logger.exception("titles %s err: %s", sku, e)

        if not cat:
            continue

        # Ozon атрибуты
        if cat.ozon_attrs:
            try:
                sys_o, usr_o = build_attributes_prompts(
                    state.name, state.brand, state.ozon_category.path,
                    sku_row["qty"], cat.ozon_attrs, cat.ozon_attr_values,
                )
                llm_o = await deps.kie.chat_json(system=sys_o, user=usr_o)
                attrs, warns = map_ozon_attributes(
                    llm_o, cat.ozon_attrs, cat.ozon_attr_values,
                )
                state.attributes_ozon[sku] = attrs  # None если required не нашёлся
                if attrs is None:
                    state.errors.append(f"ozon attrs {sku}: " + "; ".join(warns))
                else:
                    state.warnings.extend(f"{sku}: {w}" for w in warns)
            except Exception as e:
                state.errors.append(f"ozon attrs {sku}: {e}")
                state.attributes_ozon[sku] = None
                logger.exception("ozon attrs %s err: %s", sku, e)

        # WB характеристики
        if cat.wb_charcs:
            try:
                sys_w, usr_w = build_characteristics_prompts(
                    state.name, state.brand, state.wb_subject.path,
                    sku_row["qty"], cat.wb_charcs, cat.wb_charc_values,
                )
                llm_w = await deps.kie.chat_json(system=sys_w, user=usr_w)
                charcs, warns = map_wb_characteristics(
                    llm_w, cat.wb_charcs, cat.wb_charc_values,
                )
                state.characteristics_wb[sku] = charcs
                if charcs is None:
                    state.errors.append(f"wb charcs {sku}: " + "; ".join(warns))
                else:
                    state.warnings.extend(f"{sku}: {w}" for w in warns)
            except Exception as e:
                state.errors.append(f"wb charcs {sku}: {e}")
                state.characteristics_wb[sku] = None
                logger.exception("wb charcs %s err: %s", sku, e)


# ─── Этап 5: заливка Ozon ───────────────────────────────────────


def _build_ozon_item(state: ProductState, sku_row: dict[str, Any], cat: CategoryData) -> dict:
    """Собирает один item для POST /v3/product/import по правилам §5.2."""
    titles = state.titles.get(sku_row["sku"], {})
    images = state.images
    main = images.get("main") or state.src_url
    extra = images.get("extra")
    pack_url = images.get(f"pack{sku_row['qty']}") if sku_row["qty"] in (2, 3) else None
    # C6 — fallback цепочка: pack → main → src
    hero = pack_url or main or state.src_url
    image_urls = [u for u in (hero, extra) if u]
    attributes = state.attributes_ozon.get(sku_row["sku"]) or []
    return {
        "offer_id": sku_row["sku"],
        "name": titles.get("title_ozon", state.name),
        "category_id": state.ozon_category.id if state.ozon_category else 0,
        "type_id": state.ozon_category.type_id if state.ozon_category else 0,
        "price": "0",
        "old_price": "0",
        "vat": str(nds_value() / 100),  # 0.22
        "weight": sku_row["weight_packed_g"],
        "weight_unit": "g",
        "depth": sku_row["dims"].get("l", 0),
        "width": sku_row["dims"].get("w", 0),
        "height": sku_row["dims"].get("h", 0),
        "dimension_unit": "cm",
        "images": image_urls,
        "attributes": attributes,
        "description": titles.get("annotation_ozon", ""),
    }


async def upload_ozon(states: list[ProductState], cat_data: dict[tuple, CategoryData], deps: Deps) -> Report:
    rep = Report(batch_id="", total=0, successes=[], errors=[], warnings=[])
    if not settings.has_ozon_creds:
        for s in states:
            for sku_row in s.skus_3 or []:
                rep.errors.append(ReportItem(sku=sku_row["sku"], mp="ozon", reason="OZON creds not set"))
        return rep

    # Собираем items[]
    items: list[dict] = []
    sku_to_state: dict[str, ProductState] = {}
    for s in states:
        if not s.skus_3:
            continue
        cat_key = (s.ozon_category.id, s.ozon_category.type_id, s.wb_subject.id) if s.ozon_category and s.wb_subject else None
        cat = cat_data.get(cat_key) if cat_key else None
        if not cat:
            continue
        for row in s.skus_3:
            # ключ есть, значение None → required не нашёлся, SKU исключаем
            if row["sku"] in s.attributes_ozon and s.attributes_ozon[row["sku"]] is None:
                rep.errors.append(ReportItem(
                    sku=row["sku"], mp="ozon",
                    reason="required attribute missing (см. state.errors)",
                ))
                continue
            items.append(_build_ozon_item(s, row, cat))
            sku_to_state[row["sku"]] = s

    if not items:
        return rep

    try:
        task_id = await deps.ozon.import_products(items)
        result = await deps.ozon.import_wait(task_id)
        for it in result.get("result", {}).get("items") or []:
            offer_id = it.get("offer_id")
            status = it.get("status")
            if status == "imported":
                rep.successes.append(ReportItem(sku=offer_id, mp="ozon", marketplace_id=str(it.get("product_id") or "")))
            else:
                err_msg = ((it.get("errors") or [{}])[0].get("message")) or str(status)
                rep.errors.append(ReportItem(sku=offer_id, mp="ozon", reason=err_msg))
    except OzonError as e:
        for sku, s in sku_to_state.items():
            rep.errors.append(ReportItem(sku=sku, mp="ozon", reason=str(e)))

    rep.total = len(items)
    return rep


# ─── Этап 5: заливка WB ─────────────────────────────────────────


def _build_wb_card(state: ProductState, sku_row: dict[str, Any]) -> dict:
    titles = state.titles.get(sku_row["sku"], {})
    images = state.images
    main = images.get("main") or state.src_url
    extra = images.get("extra")
    pack_url = images.get(f"pack{sku_row['qty']}") if sku_row["qty"] in (2, 3) else None
    # C6 fallback
    hero = pack_url or main or state.src_url
    media = [u for u in (hero, extra) if u]
    characteristics = state.characteristics_wb.get(sku_row["sku"]) or []
    return {
        "subjectID": state.wb_subject.id if state.wb_subject else 0,
        "vendorCode": sku_row["sku"],
        "title": titles.get("title_wb_short", state.name),
        "description": titles.get("annotation_ozon", ""),
        "brand": state.brand or "",
        "dimensions": {
            "length": sku_row["dims"].get("l", 0),
            "width": sku_row["dims"].get("w", 0),
            "height": sku_row["dims"].get("h", 0),
            "weightBrutto": sku_row["weight_wb_kg"],
        },
        "characteristics": characteristics,
        "sizes": [{"techSize": "0", "wbSize": "0", "price": 0, "skus": [sku_row["sku"]]}],
        "mediaFiles": media,
    }


async def upload_wb(states: list[ProductState], cat_data: dict, deps: Deps) -> Report:
    rep = Report(batch_id="", total=0, successes=[], errors=[], warnings=[])
    if not settings.has_wb_creds:
        for s in states:
            for sku_row in s.skus_3 or []:
                rep.errors.append(ReportItem(sku=sku_row["sku"], mp="wb", reason="WB creds not set"))
        return rep

    cards: list[dict] = []
    for s in states:
        if not s.skus_3:
            continue
        for row in s.skus_3:
            if row["sku"] in s.characteristics_wb and s.characteristics_wb[row["sku"]] is None:
                rep.errors.append(ReportItem(
                    sku=row["sku"], mp="wb",
                    reason="required characteristic missing (см. state.errors)",
                ))
                continue
            cards.append(_build_wb_card(s, row))

    if not cards:
        return rep

    try:
        await deps.wb.upload_cards(cards)
        vendor_codes = [c["vendorCode"] for c in cards]
        status = await deps.wb.upload_wait(vendor_codes)
        for c in status.get("data", {}).get("cards") or []:
            vc = c.get("vendorCode")
            errs = c.get("errors") or []
            if errs:
                rep.errors.append(ReportItem(sku=vc, mp="wb", reason="; ".join(str(x) for x in errs)))
            else:
                rep.successes.append(ReportItem(sku=vc, mp="wb"))
    except WBError as e:
        for c in cards:
            rep.errors.append(ReportItem(sku=c["vendorCode"], mp="wb", reason=str(e)))

    rep.total = len(cards)
    return rep


# ─── главная корутина ────────────────────────────────────────────


async def run_batch(req: RunRequest, deps: Deps) -> None:
    """Главная точка пайплайна. Кладётся в FastAPI BackgroundTasks."""
    try:
        await deps.tg.send(
            req.chat_id,
            f"🟦 *Запускаю партию* `{req.batch_id}` ({len(req.products)} товаров)",
        )

        states = [ProductState.from_in(p) for p in req.products]
        sem = asyncio.Semaphore(settings.MAX_PARALLEL_PRODUCTS)

        # Этап 1: фото
        await asyncio.gather(*[
            process_product_images(s, req.batch_id, req.chat_id, deps, sem) for s in states
        ])
        ok_imgs = sum(1 for s in states if len(s.images) == 4)
        await deps.tg.send(req.chat_id, f"📸 Фото: {ok_imgs}/{len(states)} товаров полностью готовы")

        # Этап 2: категории
        if settings.has_ozon_creds and settings.has_wb_creds:
            try:
                ozon_tree = await deps.ozon.category_tree()
                wb_tree = await deps.wb.subjects_tree()
                ozon_leaves = _flatten_tree(ozon_tree, is_ozon=True)
                wb_leaves = _flatten_tree(wb_tree, is_ozon=False)
                await asyncio.gather(*[match_category(s, ozon_leaves, wb_leaves, deps) for s in states])
                await deps.tg.send(req.chat_id, "📂 Категории определены")
            except (OzonError, WBError) as e:
                await deps.tg.send(req.chat_id, f"⚠️ Категории: {e}")
        else:
            await deps.tg.send(req.chat_id, "⚠️ Ozon/WB ключи не заданы — пропускаю этапы 2-4. Фото залиты в S3.")
            return

        # Этап 3: справочники + шаблоны (per-category)
        unique_cats = dedup_categories(states)
        cat_data: dict[tuple, CategoryData] = {}
        for ck in unique_cats:
            try:
                cat_data[ck] = await load_category_data(ck[0], ck[1], ck[2], deps)
            except Exception as e:
                logger.exception("load_category_data %s: %s", ck, e)
        await deps.tg.send(req.chat_id, f"📋 Загружено {len(cat_data)} категорий со справочниками")

        # Этап 4: тексты + расширение SKU
        await asyncio.gather(*[build_skus_and_texts(s, cat_data, deps) for s in states])

        # Этап 5: заливка (V7 — return_exceptions, чтобы одна сторона не валила другую)
        results = await asyncio.gather(
            upload_ozon(states, cat_data, deps),
            upload_wb(states, cat_data, deps),
            return_exceptions=True,
        )
        ozon_rep = results[0] if isinstance(results[0], Report) else Report(
            batch_id=req.batch_id, total=0,
            errors=[ReportItem(sku="*", mp="ozon", reason=str(results[0])[:200])],
        )
        wb_rep = results[1] if isinstance(results[1], Report) else Report(
            batch_id=req.batch_id, total=0,
            errors=[ReportItem(sku="*", mp="wb", reason=str(results[1])[:200])],
        )

        # Финальный отчёт
        final = Report(
            batch_id=req.batch_id,
            total=ozon_rep.total + wb_rep.total,
            successes=ozon_rep.successes + wb_rep.successes,
            errors=ozon_rep.errors + wb_rep.errors,
            warnings=ozon_rep.warnings + wb_rep.warnings,
        )
        # plus per-product errors из state'ов
        for s in states:
            for err in s.errors:
                final.errors.append(ReportItem(sku=s.sku, mp="local", reason=err))

        await deps.tg.send(req.chat_id, build_final_report_md(final), parse_mode="Markdown")
    except Exception as e:
        logger.exception("run_batch fatal: %s", e)
        try:
            await deps.tg.send(req.chat_id, f"❌ Критическая ошибка: {str(e)[:500]}")
        except Exception:
            pass
