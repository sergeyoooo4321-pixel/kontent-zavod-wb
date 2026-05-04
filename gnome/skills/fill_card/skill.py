"""fill_card — обёртка над cz-backend /internal/fill_card."""
from __future__ import annotations

import os

import httpx


async def run(params: dict, ctx) -> dict:
    backend = "http://127.0.0.1:8000"
    token = os.environ.get("INTERNAL_TOKEN", "")
    body = {
        "sku": params.get("sku", ""),
        "brand": params.get("brand", ""),
        "name": params.get("name", ""),
        "images": params.get("images") or {},
        "cabinet": params.get("cabinet"),
        "dry_run": bool(params.get("dry_run", True)),
    }
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Internal-Token"] = token
    try:
        async with httpx.AsyncClient(timeout=120.0) as http:
            r = await http.post(f"{backend}/internal/fill_card",
                                headers=headers, json=body)
        if r.status_code >= 400:
            return {"ok": False, "error": f"backend HTTP {r.status_code}: {r.text[:300]}"}
        data = r.json()
        # Добавляем summary для LLM
        if data.get("ok") and data.get("dry_run"):
            data["summary"] = (
                "Payload собран в режиме DRY_RUN. Покажи юзеру что внутри и "
                "спроси одобрения на реальную публикацию."
            )
        return data
    except Exception as e:
        return {"ok": False, "error": f"backend недоступен: {str(e)[:200]}"}
