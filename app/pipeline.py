"""Pipeline-функции для генерации фото и матча категорий.

После перехода на ZIP-фабрику (см. app/internal_api.py:make_batch_zip)
здесь остались только два используемых блока:
  - Этап 1 (фото): `process_product_images` — генерация 4 изображений
    через aitunnel gpt-image-2 с identity-brief из vision LLM.
  - Этап 2 (категории): `match_category` — LLM-матчинг ozon-категории и
    wb-предмета по справочнику.

Заливка через API (`run_batch`, `upload_ozon`, `upload_wb`, сборка
payload'ов) удалена — карточки готовятся в xlsx-шаблонах и юзер сам
грузит их в кабинет МП.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx

from .config import Cabinet, settings
from .kie_ai import KieAIClient, KieAIError, KieAITimeout
from .models import CategoryRef, ProductState
from .ozon import OzonClient
from .prompts import (
    build_category_prompts,
    build_design_director_system,
    build_design_director_user,
    build_extra_prompt,
    build_main_prompt,
    build_pack_prompt,
    compile_image_prompt,
)
from .s3 import S3Client, S3Error
from .telegram import TelegramClient
from .wb import WBClient

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
        # 1. скачать из TG → S3 (src). Если src_url уже задан (гном-flow,
        # фотка пришла из S3 через bridge) — пропускаем TG-скачивание.
        if not state.src_url and state.tg_file_id:
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
        if not state.src_url:
            state.errors.append("src: ни tg_file_id, ни src_url не заданы")
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
                # aitunnel может вернуть data:URI или чистый base64 — тогда
                # нельзя fetch'нуть, нужно декодировать. fetch_or_decode_image
                # покрывает все варианты.
                content = await deps.kie.fetch_or_decode_image(kie_url)
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
        # Сначала main, потом параллельно pack2/pack3/extra. Это уменьшает burst
        # на kie.ai (4 параллельных createTask за 0.5 сек → реже Internal Error)
        # и main как «приоритетный» получает первый таймслот, не дожидаясь очереди.
        failed_modes: list[str] = []

        main_res = await _gen("main", main_prompt)
        if isinstance(main_res, BaseException):
            logger.error("main gen exception: %s", main_res)
            failed_modes.append("main")
        else:
            tag, url, err = main_res
            if url:
                state.images[tag] = url
            else:
                state.errors.append(f"{tag}: {err}")
                failed_modes.append(tag)

        results = await asyncio.gather(
            _gen("pack2", pack2_prompt),
            _gen("pack3", pack3_prompt),
            _gen("extra", extra_prompt),
            return_exceptions=True,
        )
        for res in results:
            if isinstance(res, BaseException):
                logger.error("aux gather exception: %s", res)
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
    """Отправить юзеру альбом-превью из 4 сгенерированных фото.

    Финальный ZIP с фото + xlsx-шаблонами собирается отдельно в
    internal_api.make_batch_zip — здесь только превью на лету, чтобы юзер
    видел результат генерации не дожидаясь конца партии.
    """
    tags_order = [("main", "Главное"), ("pack2", "Набор 2 шт"),
                  ("pack3", "Набор 3 шт"), ("extra", "Доп. фото")]
    photos = []
    for tag, _ in tags_order:
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

