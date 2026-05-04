"""match_category — обёртка над cz-backend /internal/match_category."""
from __future__ import annotations

import os

import httpx


async def run(params: dict, ctx) -> dict:
    backend = "http://127.0.0.1:8000"
    token = os.environ.get("INTERNAL_TOKEN", "")
    body = {
        "name": params.get("name", ""),
        "brand": params.get("brand", ""),
        "main_image_url": params.get("main_image_url"),
        "side": params.get("side", "both"),
    }
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Internal-Token"] = token
    try:
        async with httpx.AsyncClient(timeout=120.0) as http:
            r = await http.post(f"{backend}/internal/match_category",
                                headers=headers, json=body)
        if r.status_code >= 400:
            return {"ok": False, "error": f"backend HTTP {r.status_code}: {r.text[:300]}"}
        return r.json()
    except Exception as e:
        return {"ok": False, "error": f"backend недоступен: {str(e)[:200]}"}
