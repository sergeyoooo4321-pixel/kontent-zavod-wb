"""Тесты построения Markdown-отчёта."""
from app.models import Report, ReportItem
from app.reports import build_final_report_md


def test_empty_report():
    r = Report(batch_id="abc", total=0)
    md = build_final_report_md(r)
    assert "abc" in md
    assert "0/0" in md


def test_full_report():
    r = Report(
        batch_id="b1",
        total=4,
        successes=[
            ReportItem(sku="A", mp="ozon"),
            ReportItem(sku="A", mp="wb"),
        ],
        errors=[
            ReportItem(sku="Bx2", mp="ozon", field="color", reason="not in dict"),
        ],
        warnings=[
            ReportItem(sku="A", mp="wb", field="brand", reason="близкое значение"),
        ],
    )
    md = build_final_report_md(r)
    assert "b1" in md
    assert "2/4" in md
    assert "Ozon: 1, WB: 1" in md
    assert "Bx2" in md and "color" in md
    assert "Предупреждения" in md
