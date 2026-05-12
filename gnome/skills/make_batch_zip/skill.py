"""make_batch_zip — обёртка над cz-backend /internal/make_batch_zip.

Главный скилл: собирает партию в ZIP для ручной загрузки юзером.
Не дёргает API заливки маркетплейсов.
"""
from __future__ import annotations

import os

import httpx


async def run(params: dict, ctx) -> dict:
    backend = os.environ.get("BACKEND_URL", "http://127.0.0.1:8000")
    token = os.environ.get("INTERNAL_TOKEN", "")
    chat_id = ctx.chat_id  # никогда не из LLM — защита от галлюнаций
    products = params.get("products") or []
    if not products:
        return {"ok": False, "error": "products пустой"}
    if len(products) > 10:
        return {"ok": False, "error": f"максимум 10 товаров, получил {len(products)}"}

    # Защита от выдуманных src_url: подмена на реальные из bridge-кеша.
    real_urls = ctx.sessions.recent_uploads(chat_id)
    if real_urls:
        for i, p in enumerate(products):
            url = (p.get("src_url") or "").strip()
            if url not in real_urls:
                p["src_url"] = real_urls[i] if i < len(real_urls) else real_urls[-1]

    body = {
        "chat_id": int(chat_id),
        "products": products,
        "cabinet": params.get("cabinet"),
    }
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Internal-Token"] = token
    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            r = await http.post(f"{backend}/internal/make_batch_zip",
                                headers=headers, json=body)
        if r.status_code >= 400:
            return {"ok": False, "error": f"backend HTTP {r.status_code}: {r.text[:300]}"}
        data = r.json()
        return {
            "ok": data.get("ok", False),
            "batch_id": data.get("batch_id"),
            "n_products": data.get("n_products"),
            "summary": (
                f"Партия {data.get('batch_id')} запущена: "
                f"{data.get('n_products')} товаров. Прогресс и финальный "
                "ZIP-документ юзер получит в чате отдельными сообщениями. "
                "Если кеш шаблонов не покрывает какие-то категории — "
                "пайплайн попросит юзера скинуть пустые xlsx и остановится; "
                "после этого юзер напишет «собрать ещё раз»."
            ),
        }
    except Exception as e:
        return {"ok": False, "error": f"backend недоступен: {str(e)[:200]}"}
