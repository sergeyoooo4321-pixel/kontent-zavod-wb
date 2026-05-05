"""deliver_excel — обёртка над cz-backend /internal/deliver_excel.

chat_id берётся из ctx (юзер с которым гном сейчас говорит).
"""
from __future__ import annotations

import os

import httpx


async def run(params: dict, ctx) -> dict:
    backend = "http://127.0.0.1:8000"
    token = os.environ.get("INTERNAL_TOKEN", "")

    xlsx_path = (params.get("xlsx_path") or "").strip()
    if not xlsx_path:
        return {"ok": False, "error": "xlsx_path обязателен"}

    chat_id = getattr(ctx, "chat_id", None)
    if not chat_id:
        return {"ok": False, "error": "chat_id не доступен в ctx"}

    body = {
        "xlsx_path": xlsx_path,
        "chat_id": int(chat_id),
        "caption": params.get("caption"),
        "filename": params.get("filename"),
    }
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Internal-Token"] = token

    try:
        async with httpx.AsyncClient(timeout=180.0) as http:
            r = await http.post(
                f"{backend}/internal/deliver_excel",
                headers=headers, json=body,
            )
        if r.status_code >= 400:
            return {"ok": False, "error": f"backend HTTP {r.status_code}: {r.text[:300]}"}
        data = r.json()
    except Exception as e:
        return {"ok": False, "error": f"backend недоступен: {str(e)[:200]}"}

    if data.get("ok"):
        size_kb = (data.get("size_bytes") or 0) // 1024
        data["summary"] = (
            f"Отправил юзеру {data.get('sent_filename')} ({size_kb} КБ). "
            "Скажи где ему теперь грузить — Ozon: «Загрузка товаров → Excel»; "
            "WB: «Карточки → Создать → Загрузить из Excel»."
        )
    return data
