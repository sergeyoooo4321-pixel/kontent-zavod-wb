"""ask_user_choice — формирует пронумерованный список опций для юзера.

Локальный скилл: ничего не дёргает в backend, просто структурирует ввод
гнома в готовый-к-показу текст. Помечен requires_approval — после вызова
бот видит маркер [APPROVAL_REQUIRED] и ждёт ответа юзера.
"""
from __future__ import annotations

from typing import Any


def _format_score(score: Any) -> str:
    if score is None:
        return ""
    try:
        n = float(score)
    except (TypeError, ValueError):
        return ""
    if n <= 1.0:
        n = n * 100
    return f" ({int(round(n))}%)"


async def run(params: dict, ctx) -> dict:
    title = (params.get("title") or "").strip()
    options = params.get("options") or []
    allow_freetext = bool(params.get("allow_freetext", True))

    if not title:
        return {"ok": False, "error": "title обязателен"}
    if not isinstance(options, list) or not options:
        return {"ok": False, "error": "options должен быть непустым списком"}

    lines: list[str] = [title, ""]
    rendered: list[dict] = []
    for i, opt in enumerate(options, 1):
        if not isinstance(opt, dict):
            return {"ok": False, "error": f"опция #{i} должна быть объектом"}
        label = (opt.get("label") or "").strip()
        if not label:
            return {"ok": False, "error": f"опция #{i} без label"}
        detail = (opt.get("detail") or "").strip()
        score_str = _format_score(opt.get("score"))
        line = f"{i}. {label}{score_str}"
        if detail:
            line += f" — {detail}"
        lines.append(line)
        rendered.append({
            "n": i,
            "label": label,
            "id": opt.get("id"),
        })

    if allow_freetext:
        n_other = len(options) + 1
        lines.append(f"{n_other}. Другое (напиши свой вариант)")
        rendered.append({"n": n_other, "label": "Другое", "id": "__other__"})

    lines.append("")
    lines.append("Ответь цифрой выбранного варианта.")
    text = "\n".join(lines)

    return {
        "ok": True,
        "text": text,
        "options_rendered": rendered,
        "summary": (
            f"Сформирован вопрос юзеру: «{title[:80]}» — "
            f"{len(options)} вариантов{' + Другое' if allow_freetext else ''}. "
            "Покажи юзеру блок `text` БЕЗ изменений и жди ответа цифрой. "
            "В следующем шаге опции уже не показывай — юзер ответит и "
            "ты сразу применишь выбор."
        ),
    }
