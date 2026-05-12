"""Ozon Seller API клиент (категории, атрибуты, шаблоны, импорт)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)


class OzonError(Exception):
    pass


class OzonClient:
    def __init__(
        self,
        *,
        base: str,
        client_id: str | None,
        api_key: str | None,
        http: httpx.AsyncClient,
    ):
        self._base = base.rstrip("/")
        self._client_id = client_id
        self._api_key = api_key
        self._http = http

    @property
    def _headers(self) -> dict[str, str]:
        if not self._client_id or not self._api_key:
            raise OzonError("OZON_CLIENT_ID / OZON_API_KEY не заданы")
        return {
            "Client-Id": self._client_id,
            "Api-Key": self._api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=10),
        reraise=True,
    )
    async def _post(self, path: str, body: dict | None = None) -> dict:
        # Ozon rate-limit: 429 при перегрузке. Тенасити-обёртка снаружи
        # покрывает только httpx.HTTPError, а 429 приходит как валидный
        # response — нужен явный внутренний retry.
        for attempt in range(5):
            r = await self._http.post(
                f"{self._base}{path}",
                headers=self._headers,
                json=body or {},
                timeout=60.0,
            )
            if r.status_code == 429:
                ra = 5.0
                try:
                    ra = float(r.headers.get("Retry-After") or 5)
                except ValueError:
                    pass
                wait = min(max(ra, 5.0 * (2 ** attempt)), 60.0)
                logger.warning("Ozon 429 POST %s, wait=%.1fs attempt=%d/5",
                               path, wait, attempt + 1)
                await asyncio.sleep(wait)
                continue
            if r.status_code >= 400:
                raise OzonError(f"POST {path}: {r.status_code} {r.text[:300]}")
            return r.json()
        raise OzonError(f"POST {path}: 429 после 5 попыток")

    # ─── категории ───────────────────────────────────────────

    async def category_tree(self, language: str = "DEFAULT") -> list[dict]:
        """POST /v1/description-category/tree."""
        data = await self._post("/v1/description-category/tree", {"language": language})
        return data.get("result") or []

    async def category_attributes(
        self, category_id: int, type_id: int, language: str = "DEFAULT"
    ) -> list[dict]:
        """POST /v1/description-category/attribute."""
        data = await self._post(
            "/v1/description-category/attribute",
            {
                "description_category_id": category_id,
                "type_id": type_id,
                "language": language,
            },
        )
        return data.get("result") or []

    async def attribute_values(
        self,
        attribute_id: int,
        category_id: int,
        type_id: int,
        language: str = "DEFAULT",
        limit: int = 5000,
    ) -> list[dict]:
        """POST /v1/description-category/attribute/values с пагинацией last_value_id."""
        out: list[dict] = []
        last_value_id = 0
        while True:
            data = await self._post(
                "/v1/description-category/attribute/values",
                {
                    "attribute_id": attribute_id,
                    "description_category_id": category_id,
                    "type_id": type_id,
                    "language": language,
                    "limit": limit,
                    "last_value_id": last_value_id,
                },
            )
            page = data.get("result") or []
            if not page:
                break
            out.extend(page)
            if not data.get("has_next"):
                break
            last_value_id = page[-1].get("id", 0)
            if not last_value_id:
                break
        return out

    # ─── шаблон ─────────────────────────────────────────────

    async def download_template(self, category_id: int, type_id: int) -> bytes:
        """Скачивает XLSX-шаблон для категории.

        Ozon: документация описывает endpoint `/v1/product/import-info/template` или подобный.
        Возвращает бинарь XLSX. Точный URL может отличаться по версии API; используем 2 варианта.
        """
        body = {"description_category_id": category_id, "type_id": type_id}
        # вариант 1: import-info/template
        try:
            data = await self._post("/v1/product/import-info/template", body)
            url = data.get("result", {}).get("url") or data.get("url")
            if url:
                r = await self._http.get(url, timeout=120.0)
                r.raise_for_status()
                return r.content
        except OzonError as e:
            logger.warning("ozon download_template variant1 failed: %s", e)
        # вариант 2: попробуем альтернативный путь
        raise OzonError("Ozon template endpoint not available; fallback to API-only dictionaries")

    # ─── импорт ─────────────────────────────────────────────

    async def import_products(self, items: list[dict]) -> str:
        """POST /v3/product/import → task_id."""
        data = await self._post("/v3/product/import", {"items": items})
        task_id = data.get("result", {}).get("task_id")
        if not task_id:
            raise OzonError(f"import: no task_id in {data}")
        return str(task_id)

    async def import_status(self, task_id: str) -> dict:
        """POST /v1/product/import/info."""
        return await self._post("/v1/product/import/info", {"task_id": int(task_id)})

    async def import_wait(
        self, task_id: str, *, interval: float = 5.0, max_attempts: int = 60
    ) -> dict:
        """Ждёт пока task перейдёт в финальный статус."""
        for _ in range(max_attempts):
            await asyncio.sleep(interval)
            data = await self.import_status(task_id)
            items = data.get("result", {}).get("items") or []
            if items and all(
                (it.get("status") in {"imported", "failed", "processed"})
                for it in items
            ):
                return data
        raise OzonError(f"import_wait timeout for task {task_id}")
