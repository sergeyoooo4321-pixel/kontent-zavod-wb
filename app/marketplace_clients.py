from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import Settings


logger = logging.getLogger(__name__)


class MarketplaceApiError(RuntimeError):
    pass


class OzonClient:
    def __init__(self, settings: Settings):
        creds = settings.ozon_credentials
        if not creds:
            raise MarketplaceApiError("Ozon credentials are not configured")
        self.base = settings.OZON_BASE.rstrip("/")
        self.client_id, self.api_key = creds
        self.timeout = settings.HTTP_TIMEOUT_SEC

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Client-Id": self.client_id,
            "Api-Key": self.api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base}{path}", headers=self.headers, json=body)
        if response.status_code >= 400:
            raise MarketplaceApiError(f"Ozon {path} HTTP {response.status_code}: {response.text[:300]}")
        return response.json()

    async def category_tree(self) -> list[dict[str, Any]]:
        data = await self._post("/v1/description-category/tree", {"language": "DEFAULT"})
        return data.get("result") or []

    async def category_attributes(self, category_id: int, type_id: int) -> list[dict[str, Any]]:
        data = await self._post(
            "/v1/description-category/attribute",
            {
                "description_category_id": category_id,
                "type_id": type_id,
                "language": "DEFAULT",
            },
        )
        return data.get("result") or []

    async def attribute_values(
        self,
        *,
        attribute_id: int,
        category_id: int,
        type_id: int,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        last_value_id = 0
        while True:
            data = await self._post(
                "/v1/description-category/attribute/values",
                {
                    "attribute_id": attribute_id,
                    "description_category_id": category_id,
                    "type_id": type_id,
                    "language": "DEFAULT",
                    "limit": limit,
                    "last_value_id": last_value_id,
                },
            )
            page = data.get("result") or []
            out.extend(page)
            if not data.get("has_next") or not page:
                break
            last_value_id = int(page[-1].get("id") or 0)
            if not last_value_id:
                break
        return out


class WBClient:
    def __init__(self, settings: Settings):
        token = settings.wb_token
        if not token:
            raise MarketplaceApiError("WB token is not configured")
        self.base = settings.WB_BASE.rstrip("/")
        self.token = token
        self.timeout = settings.HTTP_TIMEOUT_SEC

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": self.token, "Accept": "application/json", "Content-Type": "application/json"}

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(f"{self.base}{path}", headers=self.headers, params=params)
        if response.status_code >= 400:
            raise MarketplaceApiError(f"WB {path} HTTP {response.status_code}: {response.text[:300]}")
        return response.json()

    async def subjects(self, locale: str = "ru") -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        limit = 1000
        offset = 0
        while True:
            data = await self._get("/content/v2/object/all", {"locale": locale, "limit": limit, "offset": offset})
            page = data.get("data") or []
            out.extend(page)
            if len(page) < limit:
                break
            offset += limit
        return out

    async def subject_characteristics(self, subject_id: int, locale: str = "ru") -> list[dict[str, Any]]:
        data = await self._get(f"/content/v2/object/charcs/{subject_id}", {"locale": locale})
        return data.get("data") or []

    async def directory(self, directory: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        data = await self._get(f"/content/v2/directory/{directory}", params or {"locale": "ru"})
        return data.get("data") or []
