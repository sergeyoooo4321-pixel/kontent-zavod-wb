"""Тесты validation.py — pre-flight проверки по чек-листу регламента §18."""
import pytest

from app.validation import (
    _count_sentences,
    validate_ozon_item,
    validate_ozon_item_qty,
    validate_wb_imt,
)


def _ozon_ok_item() -> dict:
    """Минимальный валидный Ozon-item."""
    return {
        "offer_id": "TEST_001",
        "name": "Synergetic Детское твердое мыло Овсяное молочко 0+ 90 г",
        "category_id": 12345,
        "type_id": 67890,
        "vat": "0.22",
        "weight": 100,
        "weight_unit": "g",
        "depth": 10, "width": 6, "height": 5,
        "dimension_unit": "cm",
        "images": ["https://s3/a.jpg"],
        "attributes": [{"id": 1, "values": [{"value": "X"}]}],
        "description": (
            "Это первое предложение. Это второе предложение про назначение. "
            "Третье — про состав. Четвёртое — про способ применения. "
            "Пятое — про результат. Шестое — кому подходит."
        ),
    }


def test_ozon_valid():
    assert validate_ozon_item(_ozon_ok_item()) == []


def test_ozon_dash_between_brand_and_product():
    """§11.2: дефис между брендом и товаром — ошибка."""
    item = _ozon_ok_item()
    item["name"] = "Tide - Стиральный порошок 400 г"
    errs = validate_ozon_item(item)
    assert any("дефис" in e.lower() for e in errs)


def test_ozon_set_prefix_required():
    """§6.2: для qty=2 префикс «Набор 2 шт» обязателен."""
    item = _ozon_ok_item()
    errs = validate_ozon_item_qty(item, qty=2)  # name без «Набор 2 шт»
    assert any("Набор 2 шт" in e for e in errs)
    item["name"] = "Набор 2 шт " + item["name"]
    errs = validate_ozon_item_qty(item, qty=2)
    assert errs == []


def test_ozon_vat_must_be_22():
    item = _ozon_ok_item()
    item["vat"] = "0.20"
    errs = validate_ozon_item(item)
    assert any("vat" in e.lower() for e in errs)


def test_ozon_no_barcode():
    item = _ozon_ok_item()
    item["barcode"] = "1234567890123"
    errs = validate_ozon_item(item)
    assert any("barcode" in e.lower() for e in errs)


def test_ozon_annotation_min_6_sentences():
    """§11.5: аннотация ≥6 предложений."""
    item = _ozon_ok_item()
    item["description"] = "Одно предложение. Второе. Третье."
    errs = validate_ozon_item(item)
    assert any("description" in e.lower() and "≥ 6" in e for e in errs)


def test_count_sentences():
    assert _count_sentences("") == 0
    assert _count_sentences("One.") == 1
    assert _count_sentences("One. Two. Three!") == 3


# ─── WB ──────────────────────────────────────────────────────────


def _wb_ok_imt() -> dict:
    return {
        "subjectID": 4459,
        "variants": [{
            "vendorCode": "TEST_001",
            "title": "Шампунь Увлажнение Персик 385 мл",
            "description": "Полное описание",
            "brand": "Timotei",
            "groupName": "Timotei_4459",
            "dimensions": {"length": 10, "width": 5, "height": 20, "weightBrutto": 0.4},
            "characteristics": [],
            "sizes": [{"techSize": "0", "wbSize": "0", "price": 0, "skus": ["TEST_001"]}],
            "mediaFiles": ["https://s3/a.jpg"],
        }]
    }


def test_wb_valid():
    assert validate_wb_imt(_wb_ok_imt(), brand="Timotei") == []


def test_wb_title_max_60():
    """§12.2: title ≤60."""
    imt = _wb_ok_imt()
    imt["variants"][0]["title"] = "А" * 65
    errs = validate_wb_imt(imt, brand="Timotei")
    assert any(">60" in e or "60" in e for e in errs)


def test_wb_title_no_brand():
    """§12.2: краткое название БЕЗ бренда."""
    imt = _wb_ok_imt()
    imt["variants"][0]["title"] = "Timotei Шампунь Персик 385 мл"
    errs = validate_wb_imt(imt, brand="Timotei")
    assert any("бренд" in e.lower() for e in errs)


def test_wb_dimensions_must_be_int():
    """§12.7: WB ждёт целые числа."""
    imt = _wb_ok_imt()
    imt["variants"][0]["dimensions"]["length"] = 10.5
    errs = validate_wb_imt(imt, brand="Timotei")
    assert any("не целое" in e for e in errs)


def test_wb_no_subject_id():
    imt = _wb_ok_imt()
    imt["subjectID"] = 0
    errs = validate_wb_imt(imt, brand="Timotei")
    assert any("subjectID" in e for e in errs)
