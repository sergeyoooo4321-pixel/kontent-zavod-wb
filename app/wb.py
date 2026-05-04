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
        """GET /content/v2/object/all — все subjects (предметы) с in-memory кешем.

        Кеш: 1 час. Subjects редко меняются, а тяжёлый запрос (8 страниц по 1000)
        провоцирует WB global limiter «too many requests per seller».
        Между страницами throttle 1.5 сек.
        """
        now = time.monotonic()
        if (
            WBClient._subjects_cache is not None
            and (now - WBClient._subjects_cache_at) < WBClient._subjects_cache_ttl
        ):
            logger.info("WB subjects_tree: %d cached", len(WBClient._subjects_cache))
            return WBClient._subjects_cache

        out: list[dict] = []
        limit = 1000
        offset = 0
        for page in range(20):  # safety: max 20k subjects
            if page > 0:
                await asyncio.sleep(1.5)  # throttle between pages
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
        WBClient._subjects_cache_at = time.monotonic()
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
