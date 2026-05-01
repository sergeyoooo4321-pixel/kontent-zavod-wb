"""Тесты normalize.py — парсер исходной строки и форматтеры названий
(регламент §7, §11.2, §12.2-12.3)."""
import pytest

from app.normalize import (
    format_ozon_title,
    format_wb_full_title,
    format_wb_short_title,
    ozon_group_name,
    parse_input_line,
    wb_group_name,
)


def test_parse_brand_dash_product():
    p = parse_input_line("Synergetic - Детское твердое мыло Овсяное молочко 0+ 90 г")
    assert p["brand"] == "Synergetic"
    assert "Овсяное молочко" in p["product_part"]
    assert p["volume"] == "90 г"
    assert "0+" in p["age_target"]


def test_parse_no_dash_uses_brand_hint():
    p = parse_input_line("Шампунь желтковый 76 г", brand_hint="Свобода")
    assert p["brand"] == "Свобода"
    assert p["product_part"] == "Шампунь желтковый 76 г"
    assert p["volume"] == "76 г"


def test_parse_volume_units_normalized():
    p1 = parse_input_line("X - Тоник 200 мл")
    assert p1["volume"] == "200 мл"
    p2 = parse_input_line("X - Шампунь 1.5 л")
    assert p2["volume"] == "1.5 л"
    p3 = parse_input_line("X - Крем 50 гр")  # «гр» → «г»
    assert p3["volume"] == "50 г"


def test_parse_no_volume():
    p = parse_input_line("X - Какой-то товар без объёма")
    assert p["volume"] == ""


# ─── format_ozon_title ───────────────────────────────────────────


def test_ozon_title_no_dash_qty1():
    """§11.2: Ozon без дефиса между брендом и товаром, qty=1 без префикса."""
    p = parse_input_line("Synergetic - Детское твердое мыло Овсяное молочко 0+ 90 г")
    title = format_ozon_title(p, qty=1)
    assert title == "Synergetic Детское твердое мыло Овсяное молочко 0+ 90 г"
    assert " - " not in title.replace("0+", "")  # дефис не должен быть между брендом и товаром


def test_ozon_title_set_prefix():
    p = parse_input_line("Tide - Стиральный порошок 400 г")
    assert format_ozon_title(p, qty=2).startswith("Набор 2 шт Tide")
    assert format_ozon_title(p, qty=3).startswith("Набор 3 шт Tide")


def test_ozon_title_no_dot_in_prefix():
    """Префикс без точки, без двоеточия (§6.2 регламента)."""
    p = parse_input_line("X - Y")
    t = format_ozon_title(p, qty=2)
    assert "Набор 2 шт " in t
    assert "Набор 2 шт." not in t
    assert "Набор 2 шт:" not in t


# ─── format_wb_short / full ──────────────────────────────────────


def test_wb_short_no_brand():
    """§12.2: краткое наименование БЕЗ бренда."""
    p = parse_input_line("Timotei - Шампунь Увлажнение Персик 385 мл")
    short = format_wb_short_title(p)
    assert "Timotei" not in short
    assert "Шампунь" in short
    assert len(short) <= 60


def test_wb_short_max_60_chars():
    p = parse_input_line(
        "Brand - Очень длинное название товара которое явно превышает 60 символов "
        "по своей сути и требует аккуратной обрезки"
    )
    short = format_wb_short_title(p)
    assert len(short) <= 60
    # не должно резать на середине слова
    assert not short.endswith("сим")


def test_wb_full_with_brand():
    """§12.3: полное наименование С брендом."""
    p = parse_input_line("Timotei - Шампунь Персик 385 мл")
    full = format_wb_full_title(p, qty=1)
    assert full.startswith("Timotei")
    full_set = format_wb_full_title(p, qty=2)
    assert full_set.startswith("Набор 2 шт Timotei")


# ─── group names ─────────────────────────────────────────────────


def test_wb_group_name_brand_subject():
    """§12.5: группа = бренд + subjectID, разные бренды НЕ в одну группу."""
    g1 = wb_group_name("Tide", 4459)
    g2 = wb_group_name("Лесной бальзам", 4459)
    assert g1 != g2  # разные бренды → разные группы
    assert "Tide" in g1
    assert "Лесной бальзам" in g2


def test_ozon_group_name_brand_dash_category():
    """§11.3: группа Ozon = «Бренд - категория»."""
    g = ozon_group_name("Лесной бальзам", "Красота / Уход / Зубные пасты")
    assert g == "Лесной бальзам - Зубные пасты"
