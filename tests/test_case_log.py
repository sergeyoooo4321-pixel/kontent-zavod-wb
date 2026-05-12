"""Тесты persistent кейс-лога (§6, §7 ТЗ)."""
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.skip(reason="тестировали удалённый pipeline.run_batch / upload_* / _build_wb_card")


@pytest.fixture
def tmp_cases_dir(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CZ_CASES_DIR", str(tmp_path))
    yield tmp_path


def test_write_case_creates_jsonl(tmp_cases_dir):
    from app import case_log
    case_log.write_case({"type": "test", "x": 1})
    files = list(tmp_cases_dir.glob("*.jsonl"))
    assert len(files) == 1
    line = files[0].read_text("utf-8").strip()
    obj = json.loads(line)
    assert obj["type"] == "test"
    assert obj["x"] == 1


def test_write_case_appends(tmp_cases_dir):
    from app import case_log
    case_log.write_case({"i": 1})
    case_log.write_case({"i": 2})
    case_log.write_case({"i": 3})
    files = list(tmp_cases_dir.glob("*.jsonl"))
    lines = files[0].read_text("utf-8").splitlines()
    assert len(lines) == 3
    assert [json.loads(l)["i"] for l in lines] == [1, 2, 3]


def test_write_batch_summary(tmp_cases_dir):
    from app import case_log
    from app.models import ReportItem
    case_log.write_batch_summary(
        batch_id="b1",
        chat_id=42,
        cabinet_names=["profit"],
        products=[{"sku": "A", "name": "Тест"}],
        successes=[ReportItem(sku="A", mp="ozon", marketplace_id="123")],
        errors=[ReportItem(sku="B", mp="wb", reason="bad")],
        warnings=[ReportItem(sku="A", mp="wb", reason="left=neighbour")],
    )
    files = list(tmp_cases_dir.glob("*.jsonl"))
    lines = files[0].read_text("utf-8").splitlines()
    types = [json.loads(l)["type"] for l in lines]
    assert types == ["batch", "sku_success", "sku_error", "sku_warning"]


def test_write_product_state(tmp_cases_dir):
    from app import case_log
    from app.models import CategoryRef, ProductState
    s = ProductState(idx=0, sku="A", name="Тест", tg_file_id="A" * 40, brand="X")
    s.images = {"main": "https://s3/A_main.jpg"}
    s.src_url = "https://s3/A_src.jpg"
    s.ozon_category = CategoryRef(id=1, type_id=2, path="Cat / Sub")
    s.warnings = ["A: подменили цвет на 'красный' (Левенштейн)"]
    case_log.write_product_state(batch_id="b1", state=s)
    files = list(tmp_cases_dir.glob("*.jsonl"))
    obj = json.loads(files[0].read_text("utf-8").strip())
    assert obj["type"] == "product_state"
    assert obj["sku"] == "A"
    assert obj["ozon_category"]["id"] == 1
    assert obj["warnings"][0].startswith("A:")


def test_wb_card_imt_format():
    """WB v2 формат: {subjectID, variants:[variant]} + groupName внутри variant."""
    from app.models import CategoryRef, ProductState
    from app.pipeline import _build_wb_card

    s = ProductState(idx=0, sku="X", name="Тест", tg_file_id="A" * 40, brand="Profit")
    s.wb_subject = CategoryRef(id=4459, path="Подкатегория")
    s.images = {"main": "https://s3/X_main.jpg"}
    s.src_url = "https://s3/X_src.jpg"

    sku_row = {"sku": "X", "qty": 1, "weight_packed_g": 100, "weight_wb_kg": 0.1,
               "dims": {"l": 10, "w": 5, "h": 3}}
    imt = _build_wb_card(s, sku_row)

    # IMT-обёртка
    assert imt["subjectID"] == 4459
    assert isinstance(imt["variants"], list) and len(imt["variants"]) == 1
    # variant внутри
    v = imt["variants"][0]
    assert v["vendorCode"] == "X"
    assert v["groupName"] == "Profit_4459"
    assert v["brand"] == "Profit"
    assert v["mediaFiles"] == ["https://s3/X_main.jpg"]


def test_wb_card_no_brand():
    """Если brand пустой — groupName всё равно валиден (sub_<id>)."""
    from app.models import CategoryRef, ProductState
    from app.pipeline import _build_wb_card

    s = ProductState(idx=0, sku="X", name="Тест", tg_file_id="A" * 40, brand=None)
    s.wb_subject = CategoryRef(id=4459)
    s.images = {"main": "https://s3/X_main.jpg"}
    s.src_url = "https://s3/X_src.jpg"

    sku_row = {"sku": "X", "qty": 1, "weight_packed_g": 100, "weight_wb_kg": 0.1,
               "dims": {"l": 10, "w": 5, "h": 3}}
    imt = _build_wb_card(s, sku_row)
    assert imt["variants"][0]["groupName"].startswith("sub_4459")
