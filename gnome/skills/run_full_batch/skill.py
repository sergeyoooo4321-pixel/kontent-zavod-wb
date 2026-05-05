"""run_full_batch — обёртка над cz-backend /internal/run_full_batch."""
from __future__ import annotations

import os

import httpx


async def run(params: dict, ctx) -> dict:
    backend = os.environ.get("BACKEND_URL", "http://127.0.0.1:8000")
    token = os.environ.get("INTERNAL_TOKEN", "")
    # chat_id берём ТОЛЬКО из ctx (текущая сессия), не из params — иначе LLM
    # может перепутать с sku/артикулом и отправить пайплайн «не в тот чат»
    chat_id = ctx.chat_id
    products = params.get("products") or []
    if not products:
        return {"ok": False, "error": "products пустой"}
    if len(products) > 10:
        return {"ok": False, "error": f"максимум 10 товаров, получил {len(products)}"}
    body = {
        "chat_id": int(chat_id),
        "products": products,
        "cabinet_names": params.get("cabinet_names"),
    }
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Internal-Token"] = token
    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            r = await http.post(f"{backend}/internal/run_full_batch",
                                headers=headers, json=body)
        if r.status_code >= 400:
            return {"ok": False, "error": f"backend HTTP {r.status_code}: {r.text[:300]}"}
        data = r.json()
        return {
            "ok": data.get("ok", False),
            "batch_id": data.get("batch_id"),
            "products_count": data.get("products_count"),
            "cabinets": data.get("cabinets") or [],
            "dry_run": data.get("dry_run", False),
            "summary": (
                f"Партия {data.get('batch_id')} запущена: "
                f"{data.get('products_count')} товаров, "
                f"кабинеты {', '.join(data.get('cabinets') or [])}, "
                f"DRY_RUN={'on' if data.get('dry_run') else 'off'}. "
                "Прогресс по этапам приходит юзеру в чат отдельными сообщениями. "
                "Тебе ждать не надо — отчёт от пайплайна юзер получит сам."
            ),
        }
    except Exception as e:
        return {"ok": False, "error": f"backend недоступен: {str(e)[:200]}"}
