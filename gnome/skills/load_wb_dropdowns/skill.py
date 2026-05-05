"""load_wb_dropdowns — подтягивает значения dropdown'ов WB через API."""
from __future__ import annotations

import os

import httpx


async def run(params: dict, ctx) -> dict:
    backend = "http://127.0.0.1:8000"
    token = os.environ.get("INTERNAL_TOKEN", "")

    body = {
        "template_json_path": (params.get("template_json_path") or "").strip(),
        "cabinet": params.get("cabinet"),
    }
    if not body["template_json_path"]:
        return {"ok": False, "error": "template_json_path обязателен"}

    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Internal-Token"] = token

    try:
        async with httpx.AsyncClient(timeout=180.0) as http:
            r = await http.post(
                f"{backend}/internal/load_wb_dropdowns",
                headers=headers, json=body,
            )
        if r.status_code >= 400:
            return {"ok": False, "error": f"backend HTTP {r.status_code}: {r.text[:300]}"}
        data = r.json()
    except Exception as e:
        return {"ok": False, "error": f"backend недоступен: {str(e)[:200]}"}

    if data.get("ok"):
        n = data.get("fields_updated", 0)
        v = data.get("fields_with_values", 0)
        data["summary"] = (
            f"WB dropdowns: проверено {n} полей, реальные значения подтянулись для {v}. "
            "JSON шаблона обновлён. Можно звать fill_excel_batch."
        )
    return data
