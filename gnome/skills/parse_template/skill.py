"""parse_template — обёртка над cz-backend /internal/parse_template.

Принимает путь к xlsx-файлу шаблона Ozon/WB на сервере, парсит структуру
полей через app.excel.parser, сохраняет JSON в ~/cz-backend/templates/
<cabinet>/. Возвращает summary для гнома + LLM-friendly summary для отчёта
юзеру.
"""
from __future__ import annotations

import os

import httpx


async def run(params: dict, ctx) -> dict:
    backend = "http://127.0.0.1:8000"
    token = os.environ.get("INTERNAL_TOKEN", "")

    body = {
        "xlsx_path": (params.get("xlsx_path") or "").strip(),
        "cabinet": params.get("cabinet"),
        "save_as": params.get("save_as"),
    }
    if not body["xlsx_path"]:
        return {"ok": False, "error": "xlsx_path обязателен"}

    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Internal-Token"] = token

    try:
        async with httpx.AsyncClient(timeout=60.0) as http:
            r = await http.post(
                f"{backend}/internal/parse_template",
                headers=headers, json=body,
            )
        if r.status_code >= 400:
            return {"ok": False, "error": f"backend HTTP {r.status_code}: {r.text[:300]}"}
        data = r.json()
    except Exception as e:
        return {"ok": False, "error": f"backend недоступен: {str(e)[:200]}"}

    if data.get("ok"):
        mp = (data.get("marketplace") or "?").upper()
        n_f = data.get("n_fields", 0)
        n_r = data.get("n_required", 0)
        n_d = data.get("n_with_dropdown", 0)
        cat_id = data.get("category_id")
        cat_str = f", category_id={cat_id}" if cat_id else ""
        data["summary"] = (
            f"Шаблон {mp} распарсен: {n_f} полей "
            f"({n_r} обязательных, {n_d} с выпадающими){cat_str}. "
            f"JSON сохранён в {data.get('saved_to')!r}. "
            f"Расскажи юзеру результат и спроси что делать дальше."
        )
    return data
