"""Wildberries Content API клиент."""
from __future__ import annotations

import asyncio
import logging

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
        r = await self._http.get(
            f"{self._base}{path}",
            headers=self._headers,
            params=params,
            timeout=60.0,
        )
        if r.status_code >= 400:
            raise WBError(f"GET {path}: {r.status_code} {r.text[:300]}")
        return r.json()

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
        """GET /content/v2/object/parent/all?locale=ru."""
        data = await self._get("/content/v2/object/parent/all", {"locale": locale})
        return data.get("data") or []

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
