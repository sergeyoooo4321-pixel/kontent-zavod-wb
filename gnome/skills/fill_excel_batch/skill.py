"""fill_excel_batch — обёртка над cz-backend /internal/fill_excel_batch."""
from __future__ import annotations

import os

import httpx


async def run(params: dict, ctx) -> dict:
    backend = "http://127.0.0.1:8000"
    token = os.environ.get("INTERNAL_TOKEN", "")

    template_json = (params.get("template_json_path") or "").strip()
    products = params.get("products") or []
    if not template_json:
        return {"ok": False, "error": "template_json_path обязателен"}
    if not isinstance(products, list) or not products:
        return {"ok": False, "error": "products должен быть непустым списком"}

    body = {
        "template_json_path": template_json,
        "products": products,
        "cabinet": params.get("cabinet"),
        "answers": params.get("answers") or {},
        "output_filename": params.get("output_filename"),
    }
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Internal-Token"] = token

    try:
        async with httpx.AsyncClient(timeout=180.0) as http:
            r = await http.post(
                f"{backend}/internal/fill_excel_batch",
                headers=headers, json=body,
            )
        if r.status_code >= 400:
            return {"ok": False, "error": f"backend HTTP {r.status_code}: {r.text[:300]}"}
        data = r.json()
    except Exception as e:
        return {"ok": False, "error": f"backend недоступен: {str(e)[:200]}"}

    state = data.get("state")
    if state == "filled":
        data["summary"] = (
            f"Готов xlsx: {data.get('xlsx_path')!r}. "
            f"Заполнено {data.get('skus_filled', 0)}/{data.get('skus_total', 0)} SKU. "
            "Используй deliver_excel чтобы отправить файл юзеру."
        )
    elif state == "pending":
        n = len(data.get("pending") or [])
        data["summary"] = (
            f"Нужны ответы юзера на {n} полей. "
            "Пройдись по списку pending: для каждого вопроса с options — вызови "
            "ask_user_choice, для freetext-полей просто спроси юзера. "
            "Накопленные ответы передай обратным вызовом fill_excel_batch."
        )
    return data
