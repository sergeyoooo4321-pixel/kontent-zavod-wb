"""generate_image — обёртка над cz-backend /internal/generate_image."""
from __future__ import annotations

import os

import httpx


async def run(params: dict, ctx) -> dict:
    backend = "http://127.0.0.1:8000"
    token = os.environ.get("INTERNAL_TOKEN", "")
    body = {
        "src_url": params.get("src_url", ""),
        "brand": params.get("brand", ""),
        "name": params.get("name", ""),
        "sku": params.get("sku", ""),
    }
    if not body["src_url"] or not body["name"]:
        return {"ok": False, "error": "src_url и name обязательны"}
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Internal-Token"] = token
    try:
        async with httpx.AsyncClient(timeout=300.0) as http:
            r = await http.post(f"{backend}/internal/generate_image",
                                headers=headers, json=body)
        if r.status_code >= 400:
            return {"ok": False, "error": f"backend HTTP {r.status_code}: {r.text[:300]}"}
        data = r.json()
        # Сжатый возврат для LLM: только теги картинок и кол-во ошибок
        return {
            "ok": data.get("ok", False),
            "images": data.get("images") or {},
            "error_count": len((data.get("errors") or {})),
            "errors": data.get("errors") or {},
            "summary": (
                f"Готово {len(data.get('images') or {})}/4 фото. "
                "Покажи юзеру и спроси одобрения."
            ),
        }
    except Exception as e:
        return {"ok": False, "error": f"backend недоступен: {str(e)[:200]}"}
