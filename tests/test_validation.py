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
    e, w = validate_ozon_item(_ozon_ok_item())
    assert e == []


def test_ozon_dash_between_brand_and_product():
    """§11.2: дефис между брендом и товаром — ошибка."""
    item = _ozon_ok_item()
    item["name"] = "Tide - Стиральный порошок 400 г"
    e, _ = validate_ozon_item(item)
    assert any("дефис" in x.lower() for x in e)


def test_ozon_set_prefix_required():
    """§6.2: для qty=2 префикс «Набор 2 шт» обязателен."""
    item = _ozon_ok_item()
    e, _ = validate_ozon_item_qty(item, qty=2)  # name без «Набор 2 шт»
    assert any("Набор 2 шт" in x for x in e)
    item["name"] = "Набор 2 шт " + item["name"]
    e, _ = validate_ozon_item_qty(item, qty=2)
    assert e == []


def test_ozon_vat_must_be_22():
    item = _ozon_ok_item()
    item["vat"] = "0.20"
    e, _ = validate_ozon_item(item)
    assert any("vat" in x.lower() for x in e)


def test_ozon_no_barcode():
    item = _ozon_ok_item()
    item["barcode"] = "1234567890123"
    e, _ = validate_ozon_item(item)
    assert any("barcode" in x.lower() for x in e)


def test_ozon_annotation_min_6_sentences():
    """§11.5: <6 предложений — warning, не критично (auto-fix дозаполнит)."""
    item = _ozon_ok_item()
    item["description"] = "Одно предложение. Второе. Третье."
    _, w = validate_ozon_item(item)
    assert any("description" in x.lower() and "≥ 6" in x for x in w)


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
    e, _ = validate_wb_imt(_wb_ok_imt(), brand="Timotei")
    assert e == []


def test_wb_title_max_60():
    """§12.2: title ≤60."""
    imt = _wb_ok_imt()
    imt["variants"][0]["title"] = "А" * 65
    e, _ = validate_wb_imt(imt, brand="Timotei")
    assert any(">60" in x or "60" in x for x in e)


def test_wb_title_no_brand():
    """§12.2: краткое название БЕЗ бренда."""
    imt = _wb_ok_imt()
    imt["variants"][0]["title"] = "Timotei Шампунь Персик 385 мл"
    e, _ = validate_wb_imt(imt, brand="Timotei")
    assert any("бренд" in x.lower() for x in e)


def test_wb_dimensions_must_be_int():
    """§12.7: WB ждёт целые числа."""
    imt = _wb_ok_imt()
    imt["variants"][0]["dimensions"]["length"] = 10.5
    e, _ = validate_wb_imt(imt, brand="Timotei")
    assert any("не целое" in x for x in e)


def test_wb_no_subject_id():
    imt = _wb_ok_imt()
    imt["subjectID"] = 0
    e, _ = validate_wb_imt(imt, brand="Timotei")
    assert any("subjectID" in x for x in e)


def test_expand_short_description_to_6_sentences():
    """auto-fix: короткая аннотация дополняется до ≥6 предложений."""
    from app.validation import expand_short_description, _count_sentences
    short = "Один. Два. Три."
    out = expand_short_description(short, brand="X", name="Y", qty=2)
    assert _count_sentences(out) >= 6


def test_expand_already_long_returns_as_is():
    from app.validation import expand_short_description, _count_sentences
    long_text = "1. 2. 3. 4. 5. 6. 7."
    assert _count_sentences(long_text) >= 6
    assert expand_short_description(long_text, "X", "Y", 1) == long_text
