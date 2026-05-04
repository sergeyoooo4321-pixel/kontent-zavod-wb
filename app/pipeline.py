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

import httpx

from .config import Cabinet, settings
from .kie_ai import KieAIClient, KieAIError, KieAITimeout
from .models import (
    CategoryRef,
    ProductState,
    Report,
    ReportItem,
    RunRequest,
)
from .mapping import map_ozon_attributes, map_wb_characteristics
from .normalize import (
    format_ozon_title,
    format_wb_full_title,
    format_wb_short_title,
    ozon_group_name,
    parse_input_line,
    wb_group_name,
)
from .ozon import OzonClient, OzonError
from .validation import (
    expand_short_description,
    validate_ozon_item,
    validate_ozon_item_qty,
    validate_wb_imt,
)
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
    ozon: OzonClient  # default-кабинет (backward-compat для api/run и тестов)
    wb: WBClient
    http: httpx.AsyncClient | None = None  # для создания per-cabinet клиентов


def _ozon_client_for(cabinet: Cabinet, deps: Deps) -> OzonClient | None:
    """Создаёт OzonClient под конкретный кабинет на общем httpx.AsyncClient."""
    if not cabinet.has_ozon:
        return None
    http = deps.http or getattr(deps.ozon, "_http", None)
    if http is None:
        return None
    return OzonClient(
        base=settings.OZON_BASE,
        client_id=cabinet.ozon.client_id,
        api_key=cabinet.ozon.api_key,
        http=http,
    )


def _wb_client_for(cabinet: Cabinet, deps: Deps) -> WBClient | None:
    if not cabinet.has_wb:
        return None
    http = deps.http or getattr(deps.wb, "_http", None)
    if http is None:
        return None
    return WBClient(base=settings.WB_BASE, token=cabinet.wb.token, http=http)


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
                kie_url = await deps.kie.generate_image_with_retry(
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
        # return_exceptions=True — один failed таск не валит остальные
        results = await asyncio.gather(
            _gen("main", main_prompt),
            _gen("pack2", pack2_prompt),
            _gen("pack3", pack3_prompt),
            _gen("extra", extra_prompt),
            return_exceptions=True,
        )
        failed_modes: list[str] = []
        for res in results:
            if isinstance(res, BaseException):
                logger.error("legacy gather got exception: %s", res)
                continue
            tag, url, err = res
            if url:
                state.images[tag] = url
            else:
                state.errors.append(f"{tag}: {err}")
                failed_modes.append(tag)

        if failed_modes:
            try:
                await deps.tg.send(
                    chat_id,
                    f"⚠️ `{state.sku}`: не сгенерировалось — {', '.join(failed_modes)}. Остальные ОК.",
                    parse_mode="Markdown",
                )
            except Exception:
                pass

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


def _flatten_tree(
    tree: list[dict],
    path: str = "",
    is_ozon: bool = True,
    parent_cat_id: int | None = None,
) -> list[dict]:
    """Сжимает дерево категорий (Ozon) или плоский список subjects (WB) в leaves.

    Ozon: рекурсивный спуск по children/types. Для leaf-типа description_category_id
    наследуется от ВЫШЕЛЕЖАЩЕЙ категории (это поле есть у уровня категории, а не типа).
    На API import_products нужны ОБА id: category_id (description_category_id) и type_id.

    WB: на входе уже плоский список subjects с {subjectID, subjectName, parentName}.
    Path = «parentName / subjectName» для лучшего keyword-match.
    """
    out: list[dict] = []
    for n in tree:
        if is_ozon:
            name = n.get("category_name") or n.get("type_name") or ""
            children = n.get("children") or n.get("types") or []
            cur_path = f"{path} / {name}" if path else name
            # description_category_id — есть только на уровне категории, не типа.
            # Сохраняем самый глубокий встретившийся вниз по дереву.
            this_cat_id = n.get("description_category_id")
            cat_id_for_children = this_cat_id if this_cat_id is not None else parent_cat_id
            if children:
                out.extend(_flatten_tree(children, cur_path, is_ozon, cat_id_for_children))
            else:
                # Leaf: type-узел; description_category_id берём от родителя
                cat_id = this_cat_id if this_cat_id is not None else parent_cat_id
                type_id = n.get("type_id")
                if cat_id and type_id:
                    out.append({
                        "id": cat_id,
                        "type_id": type_id,
                        "path": cur_path,
                    })
        else:
            # WB: плоский список subjects
            subj_id = n.get("subjectID") or n.get("subjectId")
            subj_name = n.get("subjectName") or ""
            parent = n.get("parentName") or ""
            full_path = f"{parent} / {subj_name}" if parent else subj_name
            out.append({"id": subj_id, "path": full_path})
    return out


def _filter_leaves_by_keywords(name: str, leaves: list[dict], top_n: int = 50) -> list[dict]:
    """Pre-фильтр листовых категорий по корням слов из названия товара.

    Русские слова склоняются: «стиральный» в название → «стиральные» в категории.
    Substring match не помогает. Обрезаем до 5 первых символов (русский корень)
    и матчим prefix — «стира» совпадёт и со «стиральный», и со «стиральные».

    Возвращает top-N по убыванию score; пусто, если ничего не нашлось
    (лучше пусто, чем рандом).
    """
    import re
    # Стоп-слова которые ни о чём не говорят
    stop = {"для", "под", "при", "над", "без", "над", "the", "and", "set", "kit"}
    raw = [w.lower() for w in re.findall(r"[\wа-яА-ЯёЁ]{3,}", name)]
    # Корни (5 первых символов)
    roots = {w[:5] for w in raw if w not in stop and len(w) >= 4}
    if not roots:
        return []
    valid = [l for l in leaves if l.get("id")]
    scored: list[tuple[int, dict]] = []
    for leaf in valid:
        path = (leaf.get("path") or "").lower()
        # Бонус за каждое совпадение корня
        score = sum(1 for r in roots if r in path)
        if score > 0:
            scored.append((score, leaf))
    scored.sort(key=lambda x: -x[0])
    return [l for _, l in scored[:top_n]]


async def match_category(state: ProductState, ozon_leaves: list[dict], wb_leaves: list[dict], deps: Deps) -> None:
    """Подбор категории через LLM с pre-фильтром по ключевым словам.

    Шаги:
      1. Pre-фильтр: top-50 наиболее релевантных листов из ozon/wb по словам названия
         (резко уменьшает prompt — gpt-5-2 на 1500+ категориях возвращал пустой ответ).
      2. LLM с маленьким prompt — 3 попытки с растущей температурой.
      3. Если LLM всё равно пуст — fallback на top-1 из keyword-match (без LLM).
    """
    ozon_short = _filter_leaves_by_keywords(state.name, ozon_leaves, top_n=50)
    wb_short = _filter_leaves_by_keywords(state.name, wb_leaves, top_n=50)
    logger.info("match_category %s: %d ozon + %d wb candidates", state.sku, len(ozon_short), len(wb_short))

    system, user = build_category_prompts(state.name, ozon_short, wb_short, leaves_limit=50)
    resp: dict | None = None
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            temp = settings.LLM_TEMPERATURE + (0.1 * attempt)
            resp = await deps.kie.chat_json(system=system, user=user, temperature=temp)
            if resp:
                break
        except Exception as e:
            last_err = e
            logger.warning("match_category %s attempt %d/3: %s", state.sku, attempt + 1, str(e)[:200])
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)

    if not resp:
        # Fallback: берём top-1 из keyword-match. Хоть какая-то категория лучше,
        # чем «не определена» (юзер потом сможет поправить в кабинете МП).
        o0 = next((l for l in ozon_short if l.get("id")), None)
        w0 = next((l for l in wb_short if l.get("id")), None)
        if o0 and w0:
            state.ozon_category = CategoryRef(
                id=int(o0["id"]), type_id=o0.get("type_id"), path=o0.get("path", ""), score=0.3,
            )
            state.wb_subject = CategoryRef(
                id=int(w0["id"]), path=w0.get("path", ""), score=0.3,
            )
            warn = (f"category: LLM не отвечает, использую keyword-fallback: "
                    f"ozon={o0.get('path')!r}, wb={w0.get('path')!r}")
            state.warnings.append(warn)
            logger.warning("match_category %s fallback: %s", state.sku, warn)
        else:
            state.errors.append(
                f"category: LLM не ответил и нет кандидатов keyword-match ({last_err or 'empty'})"
            )
        return

    try:
        ozon_id = int(resp.get("ozon_id") or 0)
        ozon_type_id = int(resp.get("ozon_type_id") or 0)
        wb_id = int(resp.get("wb_id") or 0)
        score = float(resp.get("score") or 0.5)
        if ozon_id:
            o = next((x for x in ozon_leaves if x.get("id") == ozon_id), None)
            state.ozon_category = CategoryRef(
                id=ozon_id, type_id=ozon_type_id or None,
                path=(o or {}).get("path", ""), score=score,
            )
        if wb_id:
            w = next((x for x in wb_leaves if x.get("id") == wb_id), None)
            state.wb_subject = CategoryRef(
                id=wb_id, path=(w or {}).get("path", ""), score=score,
            )
        # Если LLM вернул валидный JSON но ID-ы не нашлись — fallback на keyword-match
        if not state.ozon_category:
            o0 = next((l for l in ozon_short if l.get("id")), None)
            if o0:
                state.ozon_category = CategoryRef(
                    id=int(o0["id"]), type_id=o0.get("type_id"), path=o0.get("path", ""), score=0.3,
                )
                state.warnings.append(f"ozon_category: LLM-id не найден, fallback={o0.get('path')!r}")
        if not state.wb_subject:
            w0 = next((l for l in wb_short if l.get("id")), None)
            if w0:
                state.wb_subject = CategoryRef(id=int(w0["id"]), path=w0.get("path", ""), score=0.3)
                state.warnings.append(f"wb_subject: LLM-id не найден, fallback={w0.get('path')!r}")
    except Exception as e:
        state.errors.append(f"category: {e}")
        logger.exception("match_category %s parse: %s", state.sku, e)


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

    # Парсим исходное название один раз — используется для детерминированных форматтеров
    parsed = parse_input_line(state.name, brand_hint=state.brand)
    if parsed["brand"] and not state.brand:
        # Если бренд был извлечён из строки и в ProductState не задан — заполним для дальнейшего использования
        state.brand = parsed["brand"]

    # 2. LLM тексты + атрибуты + характеристики по каждой qty
    for sku_row in state.skus_3:
        sku = sku_row["sku"]
        qty = sku_row["qty"]

        # Детерминированные титулы из normalize.py (главное имя — без LLM-сюрпризов).
        # LLM в build_titles_prompts всё ещё используется ДЛЯ описания и состава (annotation_ozon,
        # composition_wb), но title_* мы пост-обрабатываем правилами регламента.
        deterministic_title_ozon = format_ozon_title(parsed, qty=qty)
        deterministic_title_wb_short = format_wb_short_title(parsed)
        deterministic_title_wb_full = format_wb_full_title(parsed, qty=qty)

        try:
            system, user = build_titles_prompts(
                state.name,
                state.brand,
                state.ozon_category.path,
                state.wb_subject.path,
                qty,
            )
            txt = await deps.kie.chat_json(system=system, user=user)
            # Title-поля — детерминированные форматтеры (LLM-варианты как fallback если форматтер пустой)
            title_ozon = deterministic_title_ozon or limit_chars(txt.get("title_ozon", ""), 500)
            title_wb_short = deterministic_title_wb_short or limit_chars(
                strip_brand(txt.get("title_wb_short", ""), state.brand), 60
            )
            title_wb_full = deterministic_title_wb_full or limit_chars(txt.get("title_wb_full", ""), 200)
            state.titles[sku] = {
                "title_ozon": title_ozon,
                "title_wb_short": title_wb_short,
                "title_wb_full": title_wb_full,
                "annotation_ozon": txt.get("annotation_ozon", ""),
                "composition_wb": limit_chars(txt.get("composition_wb", ""), 100),
            }
        except Exception as e:
            # При фейле LLM — всё равно собираем titles из детерминированных форматтеров
            state.titles[sku] = {
                "title_ozon": deterministic_title_ozon,
                "title_wb_short": deterministic_title_wb_short,
                "title_wb_full": deterministic_title_wb_full,
                "annotation_ozon": "",
                "composition_wb": "",
            }
            state.errors.append(f"titles {sku}: {e} (titles взяты из normalize)")
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


def _find_attr_id_by_name(attrs: list[dict], name_substrings: tuple[str, ...]) -> int | None:
    """Ищет id ozon-атрибута по подстроке в имени (case-insensitive)."""
    for a in attrs:
        name = (a.get("name") or "").lower()
        for sub in name_substrings:
            if sub.lower() in name:
                aid = a.get("id")
                if aid:
                    return int(aid)
    return None


def _ensure_attr(
    attributes: list[dict],
    attr_id: int,
    value: str,
    *,
    complex_id: int = 0,
    overwrite: bool = False,
) -> None:
    """Добавляет (или обновляет) атрибут в Ozon-attributes-массиве."""
    for a in attributes:
        if a.get("id") == attr_id:
            if overwrite:
                a["values"] = [{"value": str(value)}]
            return
    attributes.append({
        "complex_id": complex_id,
        "id": attr_id,
        "values": [{"value": str(value)}],
    })


def _build_ozon_item(state: ProductState, sku_row: dict[str, Any], cat: CategoryData) -> dict | None:
    """Собирает один item для POST /v3/product/import по правилам §11 регламента.

    Реализует:
      • offer_id, name (через format_ozon_title), category_id, type_id, vat=0.22
      • Габариты + вес упаковки (weight = weight_packed_g по §10.1)
      • attributes: подмешиваем «Вес товара, г» (одинаковый для qty=1/2/3)
        и «Группа» = «Бренд - категория» (§11.3) — если такие attr-id есть в категории
    """
    titles = state.titles.get(sku_row["sku"], {})
    images = state.images
    main = images.get("main") or state.src_url
    extra = images.get("extra")
    pack_url = images.get(f"pack{sku_row['qty']}") if sku_row["qty"] in (2, 3) else None
    # C6 — fallback цепочка: pack → main → src
    hero = pack_url or main or state.src_url
    image_urls = [u for u in (hero, extra) if u]
    attributes = list(state.attributes_ozon.get(sku_row["sku"]) or [])

    # §11.3 «Группа = Бренд - категория»: если в категории есть атрибут с таким именем — заполним.
    group_attr_id = _find_attr_id_by_name(cat.ozon_attrs, ("группа товаров", "группа"))
    if group_attr_id:
        cat_path = state.ozon_category.path if state.ozon_category else ""
        _ensure_attr(attributes, group_attr_id, ozon_group_name(state.brand, cat_path))

    # §10.1 «Вес товара, г»: одинаковый для qty=1/2/3 — это вес ОДИНОЧНОЙ единицы.
    unit_w = sku_row.get("weight_unit_g") or 0
    if unit_w:
        unit_attr_id = _find_attr_id_by_name(cat.ozon_attrs, ("вес товара",))
        if unit_attr_id:
            _ensure_attr(attributes, unit_attr_id, str(unit_w))

    # Auto-fix описания до ≥6 предложений (§11.5) если LLM выдала меньше
    description = expand_short_description(
        titles.get("annotation_ozon", ""),
        brand=state.brand, name=state.name, qty=sku_row["qty"],
    )

    item = {
        "offer_id": sku_row["sku"],
        "name": titles.get("title_ozon", state.name),
        "category_id": state.ozon_category.id if state.ozon_category else 0,
        "type_id": state.ozon_category.type_id if state.ozon_category else 0,
        "price": "0",
        "old_price": "0",
        "vat": str(nds_value() / 100),  # 0.22
        # §10.1 «Вес в упаковке, г»: фактический по qty, округлен вверх до 100
        "weight": sku_row["weight_packed_g"],
        "weight_unit": "g",
        "depth": sku_row["dims"].get("l", 0),
        "width": sku_row["dims"].get("w", 0),
        "height": sku_row["dims"].get("h", 0),
        "dimension_unit": "cm",
        "images": image_urls,
        "attributes": attributes,
        "description": description,
    }
    # Pre-flight: errors блокируют отправку, warnings — нет.
    e1, w1 = validate_ozon_item(item)
    e2, w2 = validate_ozon_item_qty(item, sku_row["qty"])
    errors = e1 + e2
    warnings = w1 + w2
    for w in warnings:
        state.warnings.append(f"ozon {sku_row['sku']}: {w}")
    if errors:
        for e in errors:
            state.errors.append(f"ozon {sku_row['sku']}: {e}")
        logger.warning("ozon %s SKIPPED — %d errors: %s",
                       sku_row["sku"], len(errors), errors[0])
        return None  # type: ignore[return-value]
    return item


async def _send_dry_run_payload(
    chat_id: int | None,
    deps: Deps,
    mp: str,
    endpoint: str,
    payload: dict,
    n_items: int,
) -> None:
    """DRY_RUN: шлём в TG короткое summary + JSON-документ с payload."""
    if chat_id is None:
        return
    import json as _json
    try:
        await deps.tg.send(
            chat_id,
            f"🧪 *DRY\\_RUN {mp.upper()}* — собран payload на *{n_items}* items\n"
            f"endpoint: `{endpoint}`\n"
            f"в API ничего не отправлено, JSON ниже",
            parse_mode="Markdown",
        )
        body = _json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        await deps.tg.send_document(
            chat_id, body, f"{mp}_payload.json",
            caption=f"[DRY_RUN] {mp} payload, {n_items} items",
        )
    except Exception as e:
        logger.warning("dry_run TG send fail (%s): %s", mp, e)


async def upload_ozon(
    states: list[ProductState],
    cat_data: dict[tuple, CategoryData],
    deps: Deps,
    chat_id: int | None = None,
    cabinet: Cabinet | None = None,
) -> Report:
    """Заливка на Ozon. Если cabinet задан — используем его клиента, иначе deps.ozon."""
    rep = Report(batch_id="", total=0, successes=[], errors=[], warnings=[])
    cab_label = cabinet.label if cabinet else "default"
    cab_tag = f"ozon[{cab_label}]"

    ozon_client = _ozon_client_for(cabinet, deps) if cabinet else deps.ozon
    if cabinet and not cabinet.has_ozon:
        # У этого кабинета вообще нет Ozon (например progress247 — только WB) — тихо пропускаем
        return rep
    if ozon_client is None or (not cabinet and not settings.has_ozon_creds):
        for s in states:
            for sku_row in s.skus_3 or []:
                rep.errors.append(ReportItem(sku=sku_row["sku"], mp=cab_tag,
                                             reason="OZON creds not set"))
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
            built = _build_ozon_item(s, row, cat)
            if built is None:
                # Pre-flight забраковал — не отправляем эту SKU
                rep.errors.append(ReportItem(
                    sku=row["sku"], mp=cab_tag,
                    reason="pre-flight отверг (см. state.errors)",
                ))
                continue
            items.append(built)
            sku_to_state[row["sku"]] = s

    if not items:
        return rep

    rep.total = len(items)

    # ── DRY_RUN: ничего не отправляем, шлём payload в TG ──────
    if settings.DRY_RUN:
        logger.info("[DRY_RUN] upload_ozon[%s]: %d items would be sent", cab_label, len(items))
        await _send_dry_run_payload(
            chat_id, deps, f"ozon ({cab_label})", "POST /v3/product/import",
            {"cabinet": cab_label, "items": items}, len(items),
        )
        for sku in sku_to_state:
            rep.warnings.append(ReportItem(
                sku=sku, mp=cab_tag,
                reason="[DRY_RUN] payload готов, в API не отправлено",
            ))
        return rep

    try:
        task_id = await ozon_client.import_products(items)
        result = await ozon_client.import_wait(task_id)
        for it in result.get("result", {}).get("items") or []:
            offer_id = it.get("offer_id")
            status = it.get("status")
            if status == "imported":
                rep.successes.append(ReportItem(sku=offer_id, mp=cab_tag,
                                                marketplace_id=str(it.get("product_id") or "")))
            else:
                err_msg = ((it.get("errors") or [{}])[0].get("message")) or str(status)
                rep.errors.append(ReportItem(sku=offer_id, mp=cab_tag, reason=err_msg))
    except OzonError as e:
        for sku, s in sku_to_state.items():
            rep.errors.append(ReportItem(sku=sku, mp=cab_tag, reason=str(e)))

    return rep


# ─── Этап 5: заливка WB ─────────────────────────────────────────


def _build_wb_card(state: ProductState, sku_row: dict[str, Any]) -> dict | None:
    """Возвращает IMT-объект для WB v2 в формате {subjectID, variants:[variant]}.

    WB Content API v2 ждёт массив IMT-карточек, у каждой свой subjectID и
    список variants (это разные цвета/размеры одной модели). У нас на каждый
    SKU свой IMT с одним variant.
    """
    titles = state.titles.get(sku_row["sku"], {})
    images = state.images
    main = images.get("main") or state.src_url
    extra = images.get("extra")
    pack_url = images.get(f"pack{sku_row['qty']}") if sku_row["qty"] in (2, 3) else None
    # C6 fallback
    hero = pack_url or main or state.src_url
    media = [u for u in (hero, extra) if u]
    characteristics = state.characteristics_wb.get(sku_row["sku"]) or []
    # §5.2 ТЗ: WB-группы — внутри одного бренда + одной категории.
    subject_id = state.wb_subject.id if state.wb_subject else 0
    brand = (state.brand or "").strip()
    group_name = f"{brand}_{subject_id}" if brand and subject_id else (brand or f"sub_{subject_id}")
    variant = {
        "vendorCode": sku_row["sku"],
        "title": titles.get("title_wb_short", state.name),
        "description": titles.get("annotation_ozon", ""),
        "brand": brand,
        "groupName": group_name,
        "dimensions": {
            "length": int(sku_row["dims"].get("l", 0) or 0),
            "width": int(sku_row["dims"].get("w", 0) or 0),
            "height": int(sku_row["dims"].get("h", 0) or 0),
            "weightBrutto": sku_row["weight_wb_kg"],
        },
        "characteristics": characteristics,
        "sizes": [{"techSize": "0", "wbSize": "0", "price": 0, "skus": [sku_row["sku"]]}],
        "mediaFiles": media,
    }
    imt = {"subjectID": subject_id, "variants": [variant]}
    errors, warnings = validate_wb_imt(imt, brand=brand)
    for w in warnings:
        state.warnings.append(f"wb {sku_row['sku']}: {w}")
    if errors:
        for e in errors:
            state.errors.append(f"wb {sku_row['sku']}: {e}")
        logger.warning("wb %s SKIPPED — %d errors: %s",
                       sku_row["sku"], len(errors), errors[0])
        return None  # type: ignore[return-value]
    return imt


async def upload_wb(
    states: list[ProductState],
    cat_data: dict,
    deps: Deps,
    chat_id: int | None = None,
    cabinet: Cabinet | None = None,
) -> Report:
    """Заливка на WB. Если cabinet задан — используем его клиента, иначе deps.wb."""
    rep = Report(batch_id="", total=0, successes=[], errors=[], warnings=[])
    cab_label = cabinet.label if cabinet else "default"
    cab_tag = f"wb[{cab_label}]"

    wb_client = _wb_client_for(cabinet, deps) if cabinet else deps.wb
    if cabinet and not cabinet.has_wb:
        return rep
    if wb_client is None or (not cabinet and not settings.has_wb_creds):
        for s in states:
            for sku_row in s.skus_3 or []:
                rep.errors.append(ReportItem(sku=sku_row["sku"], mp=cab_tag,
                                             reason="WB creds not set"))
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
            built = _build_wb_card(s, row)
            if built is None:
                rep.errors.append(ReportItem(
                    sku=row["sku"], mp=cab_tag,
                    reason="pre-flight отверг (см. state.errors)",
                ))
                continue
            cards.append(built)

    if not cards:
        return rep

    # cards теперь = [{"subjectID":..., "variants":[{...}]}], vendor-коды из variants
    def _vendors(imt_list: list[dict]) -> list[str]:
        out: list[str] = []
        for imt in imt_list:
            for v in imt.get("variants") or []:
                if v.get("vendorCode"):
                    out.append(v["vendorCode"])
        return out

    vendor_codes = _vendors(cards)
    rep.total = len(vendor_codes)

    # ── DRY_RUN: ничего не отправляем, шлём payload в TG ──────
    if settings.DRY_RUN:
        logger.info("[DRY_RUN] upload_wb[%s]: %d cards would be sent", cab_label, len(vendor_codes))
        await _send_dry_run_payload(
            chat_id, deps, f"wb ({cab_label})", "POST /content/v2/cards/upload",
            {"cabinet": cab_label, "cards": cards}, len(vendor_codes),
        )
        for vc in vendor_codes:
            rep.warnings.append(ReportItem(
                sku=vc, mp=cab_tag,
                reason="[DRY_RUN] payload готов, в API не отправлено",
            ))
        return rep

    try:
        await wb_client.upload_cards(cards)
        status = await wb_client.upload_wait(vendor_codes)
        for c in status.get("data", {}).get("cards") or []:
            vc = c.get("vendorCode")
            errs = c.get("errors") or []
            if errs:
                rep.errors.append(ReportItem(sku=vc, mp=cab_tag,
                                             reason="; ".join(str(x) for x in errs)))
            else:
                rep.successes.append(ReportItem(sku=vc, mp=cab_tag))
    except WBError as e:
        for vc in vendor_codes:
            rep.errors.append(ReportItem(sku=vc, mp=cab_tag, reason=str(e)))

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

        # Какие кабинеты используем для заливки
        target_cabinets: list[Cabinet] = []
        if req.cabinet_names:
            for name in req.cabinet_names:
                c = settings.get_cabinet(name)
                if c:
                    target_cabinets.append(c)
                else:
                    logger.warning("run_batch: cabinet '%s' not found", name)
        else:
            # backward-compat: один default-кабинет
            default_name = settings.default_cabinet_name
            if default_name:
                c = settings.get_cabinet(default_name)
                if c:
                    target_cabinets.append(c)

        if not target_cabinets:
            await deps.tg.send(req.chat_id,
                               "⚠️ Этап 5 пропущен: ни один кабинет не настроен.")
            return

        cab_labels = ", ".join(c.label for c in target_cabinets)
        if settings.DRY_RUN:
            await deps.tg.send(
                req.chat_id,
                "🧪 *DRY\\_RUN* включён — Этап 5: payload-ы соберутся "
                "и придут в чат как JSON-документы, в API маркетплейсов "
                "ничего не отправляется.\n"
                f"Целевые кабинеты: *{cab_labels}*",
                parse_mode="Markdown",
            )
        else:
            await deps.tg.send(
                req.chat_id,
                f"🚚 Этап 5: заливка в *{len(target_cabinets)}* кабинет(а): {cab_labels}",
                parse_mode="Markdown",
            )

        # Этап 5: для каждого кабинета параллельно ozon + wb
        # gather с return_exceptions, чтобы один проблемный кабинет не валил остальные
        all_reports: list[Report] = []
        for cab in target_cabinets:
            cab_results = await asyncio.gather(
                upload_ozon(states, cat_data, deps, chat_id=req.chat_id, cabinet=cab),
                upload_wb(states, cat_data, deps, chat_id=req.chat_id, cabinet=cab),
                return_exceptions=True,
            )
            for res in cab_results:
                if isinstance(res, Report):
                    all_reports.append(res)
                else:
                    all_reports.append(Report(
                        batch_id=req.batch_id, total=0,
                        errors=[ReportItem(sku="*", mp=cab.label, reason=str(res)[:200])],
                    ))

        # Финальный отчёт = сумма по всем кабинетам
        final = Report(batch_id=req.batch_id, total=0)
        for r in all_reports:
            final.total += r.total
            final.successes.extend(r.successes)
            final.errors.extend(r.errors)
            final.warnings.extend(r.warnings)
        # plus per-product errors из state'ов
        for s in states:
            for err in s.errors:
                final.errors.append(ReportItem(sku=s.sku, mp="local", reason=err))

        # §6, §7 ТЗ: персистентный кейс-лог для последующего обучения
        try:
            from . import case_log
            case_log.write_batch_summary(
                batch_id=req.batch_id,
                chat_id=req.chat_id,
                cabinet_names=req.cabinet_names,
                products=[{"sku": p.sku, "name": p.name} for p in req.products],
                successes=final.successes,
                errors=final.errors,
                warnings=final.warnings,
            )
            for s in states:
                case_log.write_product_state(batch_id=req.batch_id, state=s)
        except Exception as e:
            logger.warning("case_log write failed: %s", e)

        await deps.tg.send(req.chat_id, build_final_report_md(final), parse_mode="Markdown")
    except Exception as e:
        logger.exception("run_batch fatal: %s", e)
        try:
            await deps.tg.send(req.chat_id, f"❌ Критическая ошибка: {str(e)[:500]}")
        except Exception:
            pass
