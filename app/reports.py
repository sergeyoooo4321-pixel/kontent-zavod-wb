"""Формирование Markdown-отчёта и промежуточных статусных сообщений."""
from __future__ import annotations

from .models import Report, ReportItem


def _esc(s: str) -> str:
    """Минимальный escape для Telegram Markdown."""
    return str(s).replace("_", r"\_").replace("*", r"\*").replace("`", r"\`").replace("[", r"\[")


def build_progress_msg(stage: str, **stats: int | str) -> str:
    parts = [stage]
    for k, v in stats.items():
        parts.append(f"{k}={v}")
    return " | ".join(parts)


def build_final_report_md(report: Report) -> str:
    """Финальный Markdown-отчёт. ТЗ §6/§8."""
    lines: list[str] = []
    lines.append(f"*Партия `{_esc(report.batch_id)}` — итог.*")
    lines.append(f"Опубликовано: *{len(report.successes)}/{report.total}*")
    lines.append("")

    if report.successes:
        ozon_ok = [i for i in report.successes if i.mp == "ozon"]
        wb_ok = [i for i in report.successes if i.mp == "wb"]
        lines.append(f"✅ Ozon: {len(ozon_ok)}, WB: {len(wb_ok)}")

    if report.errors:
        lines.append("")
        lines.append("*Ошибки:*")
        for it in report.errors[:30]:
            field = f" [{_esc(it.field)}]" if it.field else ""
            reason = _esc(it.reason or "")
            lines.append(f"❌ {it.mp.upper()} `{_esc(it.sku)}`{field}: {reason}")
        if len(report.errors) > 30:
            lines.append(f"...и ещё {len(report.errors) - 30} ошибок")

    if report.warnings:
        lines.append("")
        lines.append("*Предупреждения (подобрано ближайшее значение):*")
        for it in report.warnings[:15]:
            field = f" [{_esc(it.field)}]" if it.field else ""
            reason = _esc(it.reason or "")
            lines.append(f"⚠️ {it.mp.upper()} `{_esc(it.sku)}`{field}: {reason}")
        if len(report.warnings) > 15:
            lines.append(f"...и ещё {len(report.warnings) - 15}")

    return "\n".join(lines)


def build_partial_report_md(report: Report, batch_id: str, stage: str) -> str:
    """Промежуточный отчёт по этапу."""
    return (
        f"*{stage}* — партия `{_esc(batch_id)}`: "
        f"OK={len(report.successes)}, ошибок={len(report.errors)}, "
        f"предупреждений={len(report.warnings)}"
    )
