"""Wildberries Content API клиент."""
from __future__ import annotations

import asyncio
import logging
import time

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)


class WBError(Exception):
    pass


class WBClient:
    # Кеш subjects_tree — он редко меняется, тяжёлый запрос (8 страниц по 1000)
    _subjects_cache: list[dict] | None = None
    _subjects_cache_at: float = 0.0
    _subjects_cache_ttl: float = 3600.0  # 1 час

    def __init__(self, *, base: str, token: str | None, http: httpx.AsyncClient):
        self._base = base.rstrip("/")
        self._token = token
        self._http = http

    @property
    def _headers(self) -> dict[str, str]:
        if not self._token:
            raise WBError("WB_TOKEN не задан")
        return {
            "Authorization": self._token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=10),
        reraise=True,
    )
    async def _get(self, path: str, params: dict | None = None) -> dict:
        # Доп. retry на 429 (WB global limiter per seller)
        for attempt in range(5):
            r = await self._http.get(
                f"{self._base}{path}",
                headers=self._headers,
                params=params,
                timeout=60.0,
            )
            if r.status_code == 429:
                ra = 5.0
                try:
                    ra = float(r.headers.get("Retry-After") or 5)
                except ValueError:
                    pass
                wait = min(max(ra, 5.0 * (2 ** attempt)), 60.0)
                logger.warning("WB 429 GET %s, wait=%.1fs attempt=%d/5", path, wait, attempt + 1)
                await asyncio.sleep(wait)
                continue
            if r.status_code >= 400:
                raise WBError(f"GET {path}: {r.status_code} {r.text[:300]}")
            return r.json()
        raise WBError(f"GET {path}: 429 после 5 попыток")

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=10),
        reraise=True,
    )
    async def _post(self, path: str, body: dict | None = None) -> dict:
        r = await self._http.post(
            f"{self._base}{path}",
            headers=self._headers,
            json=body or {},
            timeout=60.0,
        )
        if r.status_code >= 400:
            raise WBError(f"POST {path}: {r.status_code} {r.text[:300]}")
        return r.json()

    # ─── категории/предметы ─────────────────────────────────

    async def subjects_tree(self, locale: str = "ru") -> list[dict]:
        """GET /content/v2/object/all — все subjects c кешем in-memory + disk.

        Кеш TTL: 6 часов. Subjects редко меняются (~раз в неделю), а тяжёлый
        запрос (~7-10 страниц по 1000) провоцирует WB «too many requests
        per seller». Disk-кеш переживает рестарт сервиса.
        Между страницами throttle 1.5 сек.
        """
        import json as _json
        from pathlib import Path

        now = time.monotonic()
        # 1. In-memory кеш (быстрый путь между запросами в одном процессе)
        if (
            WBClient._subjects_cache is not None
            and (now - WBClient._subjects_cache_at) < WBClient._subjects_cache_ttl
        ):
            logger.info("WB subjects_tree: %d (memory cache)", len(WBClient._subjects_cache))
            return WBClient._subjects_cache

        # 2. Disk-кеш (переживает рестарт)
        cache_path = Path.home() / "cz-backend" / "cache" / f"wb_subjects_{locale}.json"
        cache_ttl_disk = 6 * 3600  # 6 часов
        if cache_path.exists():
            try:
                age = time.time() - cache_path.stat().st_mtime
                if age < cache_ttl_disk:
                    data = _json.loads(cache_path.read_text("utf-8"))
                    if isinstance(data, list) and data:
                        WBClient._subjects_cache = data
                        WBClient._subjects_cache_at = now
                        logger.info("WB subjects_tree: %d (disk cache, age=%.0fs)", len(data), age)
                        return data
            except Exception as e:
                logger.warning("WB disk cache read fail: %s", e)

        # 3. Свежая загрузка
        out: list[dict] = []
        limit = 1000
        offset = 0
        for page in range(20):
            if page > 0:
                await asyncio.sleep(1.5)
            data = await self._get("/content/v2/object/all",
                                   {"locale": locale, "limit": limit, "offset": offset})
            chunk = data.get("data") or []
            if not chunk:
                break
            out.extend(chunk)
            if len(chunk) < limit:
                break
            offset += limit
        WBClient._subjects_cache = out
        WBClient._subjects_cache_at = now
        # Сохраняем на диск (best effort)
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(_json.dumps(out, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.warning("WB disk cache write fail: %s", e)
        logger.info("WB subjects_tree: %d total subjects (fresh)", len(out))
        return out

    async def subject_charcs(self, subject_id: int, locale: str = "ru") -> list[dict]:
        """GET /content/v2/object/charcs/{id}?locale=ru."""
        data = await self._get(f"/content/v2/object/charcs/{subject_id}", {"locale": locale})
        return data.get("data") or []

    async def directory_values(self, name: str, locale: str = "ru") -> list[dict]:
        """GET /content/v2/directory/{name}?locale=ru."""
        data = await self._get(f"/content/v2/directory/{name}", {"locale": locale})
        return data.get("data") or []

    # ─── заливка карточек ───────────────────────────────────

    async def upload_cards(self, cards: list[dict]) -> dict:
        """POST /content/v2/cards/upload — синхронный ответ."""
        return await self._post("/content/v2/cards/upload", cards)  # WB ждёт массив в корне

    async def upload_status(
        self,
        vendor_codes: list[str] | None = None,
        *,
        sort: str = "updateAt",
        order: str = "desc",
        limit: int = 1000,
    ) -> dict:
        """POST /content/v2/cards/upload/list — возвращает per-card статус."""
        body: dict = {
            "settings": {
                "sort": {"sortColumn": sort, "ascending": order == "asc"},
                "filter": {"textSearch": "", "allowedCategoriesOnly": True},
                "cursor": {"limit": limit},
            }
        }
        if vendor_codes:
            body["settings"]["filter"]["vendorCodes"] = vendor_codes
        return await self._post("/content/v2/cards/upload/list", body)

    async def upload_wait(
        self, vendor_codes: list[str], *, interval: float = 10.0, max_attempts: int = 30
    ) -> dict:
        """Опрос статуса заливки до получения per-vendor статусов."""
        for _ in range(max_attempts):
            await asyncio.sleep(interval)
            data = await self.upload_status(vendor_codes)
            cards = data.get("data", {}).get("cards") or []
            if cards:
                return data
        raise WBError(f"upload_wait timeout for {len(vendor_codes)} vendor_codes")

    async def list_upload_errors(self) -> list[dict]:
        """POST /content/v2/cards/error/list — карточки которые не прошли валидацию.

        КРИТИЧНО для надёжной заливки: WB возвращает HTTP 200 на /cards/upload
        даже когда карточка свалилась — настоящие ошибки лежат тут. Опрашивать
        через 5-30 сек после upload, читать поле `errors` по каждому
        vendorCode'у. Если vendorCode сюда попал — карточка попала в Черновики
        и НЕ опубликована.
        """
        data = await self._post("/content/v2/cards/error/list", {})
        return data.get("data") or []

    async def upload_wait_with_errors(
        self,
        vendor_codes: list[str],
        *,
        interval: float = 5.0,
        max_attempts: int = 12,
    ) -> dict:
        """Двухфазный poll: ждём появления карточек ИЛИ появления их в error/list.

        Возвращает dict:
          {
            "succeeded": [{vendorCode, nmID}, ...],
            "failed":    [{vendorCode, errors: [str], updateAt}, ...],
            "pending":   [vendorCode, ...]   # ни тут, ни там
          }
        """
        wanted = set(vendor_codes)
        succeeded: list[dict] = []
        failed: list[dict] = []
        seen_succeeded: set[str] = set()
        seen_failed: set[str] = set()

        for _ in range(max_attempts):
            await asyncio.sleep(interval)
            # 1) Список с ошибками (Черновики)
            try:
                err_items = await self.list_upload_errors()
            except WBError as e:
                logger.warning("error/list fail: %s", e)
                err_items = []
            for item in err_items:
                vc = item.get("vendorCode") or item.get("object", {}).get("vendorCode")
                if vc in wanted and vc not in seen_failed:
                    failed.append({
                        "vendorCode": vc,
                        "errors": item.get("errors") or [],
                        "updateAt": item.get("updateAt") or item.get("updatedAt"),
                    })
                    seen_failed.add(vc)
            # 2) Список созданных карточек
            try:
                status = await self.upload_status(list(wanted))
                cards = status.get("data", {}).get("cards") or []
            except WBError as e:
                logger.warning("upload_status fail: %s", e)
                cards = []
            for c in cards:
                vc = c.get("vendorCode")
                if vc in wanted and vc not in seen_succeeded and vc not in seen_failed:
                    nm = None
                    for sz in (c.get("sizes") or []):
                        if sz.get("chrtID") or sz.get("nmID"):
                            nm = sz.get("nmID") or c.get("nmID")
                            break
                    succeeded.append({"vendorCode": vc, "nmID": nm or c.get("nmID")})
                    seen_succeeded.add(vc)
            # Если все vendor_codes разобрались (succeeded ∪ failed = wanted) — выходим
            if (seen_succeeded | seen_failed) >= wanted:
                break
        pending = [v for v in vendor_codes if v not in seen_succeeded and v not in seen_failed]
        return {"succeeded": succeeded, "failed": failed, "pending": pending}

    # ─── загрузка картинок (отдельным запросом ПОСЛЕ карточки) ──────────

    async def media_save(self, *, nm_id: int, image_urls: list[str]) -> dict:
        """POST /content/v3/media/save — заливка картинок по URL для nmID.

        КРИТИЧНО: WB ждёт что картинки будут залиты ОТДЕЛЬНЫМ запросом, ПОСЛЕ
        успешного создания карточки (когда уже есть nmID). Если слать
        картинки в `/cards/upload` — приходит ошибка
        «no product card found for this article».

        Требования к URL: только https, без редиректов, без presigned-query,
        путь должен заканчиваться на `.jpg|.jpeg|.png`. Минимум 900×1200, ≤10 МБ.
        """
        body = {"nmId": int(nm_id), "data": list(image_urls)}
        return await self._post("/content/v3/media/save", body)
