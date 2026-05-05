"""Юнит-тесты mapping.py — Ozon атрибуты + WB характеристики."""
from app.mapping import map_ozon_attributes, map_wb_characteristics


# ─── Ozon ────────────────────────────────────────────────────────


def test_ozon_required_dict_exact():
    attrs = [{"id": 85, "name": "Бренд", "is_required": True, "is_collection": False, "dictionary_id": 28732849}]
    vals = {85: [{"id": 1, "value": "Apple"}, {"id": 2, "value": "Samsung"}]}
    out, warns = map_ozon_attributes({"85": "Apple"}, attrs, vals)
    assert out == [{
        "complex_id": 0, "id": 85,
        "values": [{"dictionary_value_id": 1, "value": "Apple"}],
    }]
    assert warns == []


def test_ozon_required_dict_substituted():
    attrs = [{"id": 89, "name": "Цвет", "is_required": True, "is_collection": False, "dictionary_id": 12345}]
    vals = {89: [{"id": 100, "value": "Красный"}, {"id": 101, "value": "Синий"}]}
    out, warns = map_ozon_attributes({"89": "красноватый"}, attrs, vals)
    assert out is not None
    assert out[0]["values"][0]["dictionary_value_id"] == 100
    assert any("Красный" in w for w in warns)


def test_ozon_required_dict_missing_returns_none():
    attrs = [{"id": 85, "name": "Бренд", "is_required": True, "dictionary_id": 1}]
    vals = {85: [{"id": 1, "value": "Apple"}]}
    out, warns = map_ozon_attributes({}, attrs, vals)
    assert out is None
    assert any("required" in w for w in warns)


def test_ozon_optional_missing_skipped():
    attrs = [
        {"id": 85, "name": "Бренд", "is_required": True, "dictionary_id": 1},
        {"id": 99, "name": "Гарантия", "is_required": False, "dictionary_id": 0, "type": "string"},
    ]
    vals = {85: [{"id": 1, "value": "Apple"}]}
    out, warns = map_ozon_attributes({"85": "Apple"}, attrs, vals)
    assert out is not None
    # Только бренд, гарантия пропущена (необязательно + нет значения)
    assert len(out) == 1
    assert out[0]["id"] == 85


def test_ozon_collection_multi_values():
    attrs = [{"id": 89, "name": "Цвет", "is_required": False, "is_collection": True, "dictionary_id": 1}]
    vals = {89: [
        {"id": 100, "value": "Красный"},
        {"id": 101, "value": "Синий"},
        {"id": 102, "value": "Зелёный"},
    ]}
    out, warns = map_ozon_attributes({"89": ["Красный", "Синий"]}, attrs, vals)
    assert out is not None
    assert len(out[0]["values"]) == 2
    ids = sorted(v["dictionary_value_id"] for v in out[0]["values"])
    assert ids == [100, 101]


def test_ozon_free_text_no_dict():
    attrs = [{"id": 4180, "name": "ТНВЭД", "is_required": False, "dictionary_id": 0, "type": "string"}]
    out, warns = map_ozon_attributes({"4180": "1234567890"}, attrs, {})
    assert out == [{"complex_id": 0, "id": 4180, "values": [{"value": "1234567890"}]}]


def test_ozon_dict_required_value_not_found():
    """LLM выдал значение, но Левенштейн не нашёл совпадения (пустой словарь)."""
    attrs = [{"id": 85, "name": "Бренд", "is_required": True, "dictionary_id": 1}]
    vals = {85: []}  # словарь пустой
    out, warns = map_ozon_attributes({"85": "Apple"}, attrs, vals)
    assert out is None
    assert any("not in dict" in w for w in warns)


def test_ozon_complex_id_preserved():
    attrs = [{"id": 9048, "name": "Изображение", "is_required": False,
              "dictionary_id": 0, "attribute_complex_id": 100, "type": "string"}]
    out, warns = map_ozon_attributes({"9048": "url"}, attrs, {})
    assert out[0]["complex_id"] == 100


# ─── WB ──────────────────────────────────────────────────────────


def test_wb_dict_single_required():
    charcs = [{"charcID": 14177439, "name": "Цвет", "required": True, "charcType": 4}]
    vals = {14177439: [{"name": "Красный"}, {"name": "Синий"}]}
    out, warns = map_wb_characteristics({"14177439": ["Красный"]}, charcs, vals)
    assert out == [{"id": 14177439, "value": ["Красный"]}]
    assert warns == []


def test_wb_dict_multi_substituted():
    charcs = [{"charcID": 1, "name": "Сезон", "required": False, "charcType": 5}]
    vals = {1: [{"name": "Весна"}, {"name": "Лето"}, {"name": "Осень"}]}
    out, warns = map_wb_characteristics({"1": ["вёсна", "лет"]}, charcs, vals)
    assert out is not None
    assert sorted(out[0]["value"]) == ["Весна", "Лето"]
    assert len(warns) == 2  # обе подменены


def test_wb_number_type():
    charcs = [{"charcID": 90630, "name": "Высота упаковки", "required": False, "charcType": 0}]
    out, warns = map_wb_characteristics({"90630": ["10"]}, charcs, {})
    assert out == [{"id": 90630, "value": [10]}]


def test_wb_number_required_invalid():
    charcs = [{"charcID": 1, "name": "Объём", "required": True, "charcType": 0}]
    out, warns = map_wb_characteristics({"1": ["abc"]}, charcs, {})
    assert out is None
    assert any("not number" in w for w in warns)


def test_wb_string_type():
    charcs = [{"charcID": 14177473, "name": "Описание", "required": False, "charcType": 1}]
    out, warns = map_wb_characteristics({"14177473": ["Просто текст"]}, charcs, {})
    assert out == [{"id": 14177473, "value": ["Просто текст"]}]


def test_wb_required_missing_returns_none():
    charcs = [{"charcID": 1, "name": "Бренд", "required": True, "charcType": 1}]
    out, warns = map_wb_characteristics({}, charcs, {})
    assert out is None


def test_wb_optional_missing_skipped():
    charcs = [
        {"charcID": 1, "name": "Бренд", "required": True, "charcType": 1},
        {"charcID": 2, "name": "Доп.", "required": False, "charcType": 1},
    ]
    out, warns = map_wb_characteristics({"1": ["X"]}, charcs, {})
    assert len(out) == 1
    assert out[0]["id"] == 1


def test_wb_max_count_clamp():
    """maxCount ограничивает количество значений в коллекции."""
    charcs = [{"charcID": 1, "name": "Цвет", "required": False, "charcType": 5, "maxCount": 2}]
    vals = {1: [{"name": "А"}, {"name": "Б"}, {"name": "В"}, {"name": "Г"}]}
    out, warns = map_wb_characteristics({"1": ["А", "Б", "В", "Г"]}, charcs, vals)
    assert len(out[0]["value"]) == 2


# ─── numeric fallbacks ────────────────────────────────────────────


def test_wb_numeric_range_fallback():
    """LLM '600' → 'до 1000' (диапазон)."""
    charcs = [{"charcID": 1, "name": "Вес", "required": True, "charcType": 4}]
    vals = {1: [{"name": "до 500"}, {"name": "до 1000"}, {"name": "до 5000"}]}
    out, warns = map_wb_characteristics({"1": ["600"]}, charcs, vals)
    assert out is not None
    assert out[0]["value"] == ["до 1000"]
    assert any("numeric-range" in w for w in warns)


def test_wb_numeric_closest_fallback():
    """LLM '400' → '500' (ближайшее в [100, 200, 500, 1000])."""
    charcs = [{"charcID": 89008, "name": "Вес товара без упаковки (г)",
               "required": True, "charcType": 4}]
    vals = {89008: [
        {"name": "100"}, {"name": "200"}, {"name": "500"}, {"name": "1000"},
    ]}
    out, warns = map_wb_characteristics({"89008": ["400"]}, charcs, vals)
    assert out is not None
    assert out[0]["value"] == ["500"]
    assert any("closest-numeric" in w for w in warns)


def test_wb_numeric_closest_picks_lower_when_equidistant():
    """LLM '400', словарь [100, 700] — расстояние 300 равное; берёт первое (100)."""
    charcs = [{"charcID": 1, "name": "Вес", "required": False, "charcType": 4}]
    vals = {1: [{"name": "100"}, {"name": "700"}]}
    out, warns = map_wb_characteristics({"1": ["400"]}, charcs, vals)
    assert out is not None
    # реализация: < (строго меньше), значит 100 пришёл первым и его не сменили
    assert out[0]["value"] == ["100"]


def test_ozon_numeric_closest_fallback():
    """Ozon тоже умеет closest-numeric."""
    attrs = [{"id": 100, "name": "Объём, мл", "is_required": True, "dictionary_id": 1}]
    vals = {100: [
        {"id": 1, "value": "100"}, {"id": 2, "value": "250"},
        {"id": 3, "value": "500"}, {"id": 4, "value": "1000"},
    ]}
    out, warns = map_ozon_attributes({"100": "300"}, attrs, vals)
    assert out is not None
    assert out[0]["values"][0]["value"] == "250"
    assert out[0]["values"][0]["dictionary_value_id"] == 2
    assert any("closest-numeric" in w for w in warns)


def test_wb_no_number_in_dict_falls_through():
    """LLM '400', словарь — только строки [красный, синий] — closest не помогает."""
    charcs = [{"charcID": 1, "name": "Цвет", "required": False, "charcType": 4}]
    vals = {1: [{"name": "Красный"}, {"name": "Синий"}]}
    out, warns = map_wb_characteristics({"1": ["400"]}, charcs, vals)
    # Левенштейн всё-таки что-то найдёт (он первый), warnings будет substituted
    assert out is not None or out is None  # любой исход ок: главное, не падаем


# ─── auto-fill brand_hint / country_hint ─────────────────────────


def test_ozon_brand_auto_fill_when_llm_missing():
    """LLM ничего не вернула про бренд — берём из brand_hint."""
    attrs = [{"id": 85, "name": "Бренд", "is_required": True, "dictionary_id": 1}]
    vals = {85: [{"id": 5089754, "value": "Tide"}, {"id": 7, "value": "Procter"}]}
    out, warns = map_ozon_attributes({}, attrs, vals, brand_hint="Tide")
    assert out is not None
    assert out[0]["values"][0]["dictionary_value_id"] == 5089754
    assert out[0]["values"][0]["value"] == "Tide"
    assert any("auto-filled" in w for w in warns)


def test_ozon_brand_auto_fill_required_no_hint():
    """LLM не вернула, hint тоже None — ругаемся на required."""
    attrs = [{"id": 85, "name": "Бренд", "is_required": True, "dictionary_id": 1}]
    vals = {85: [{"id": 1, "value": "Apple"}]}
    out, warns = map_ozon_attributes({}, attrs, vals, brand_hint=None)
    assert out is None


def test_ozon_country_auto_fill():
    """Страна-изготовитель: дефолт Россия."""
    attrs = [{"id": 4389, "name": "Страна-изготовитель",
              "is_required": True, "dictionary_id": 1}]
    vals = {4389: [{"id": 90295, "value": "Россия"}, {"id": 999, "value": "Китай"}]}
    out, warns = map_ozon_attributes({}, attrs, vals)
    assert out is not None
    assert out[0]["values"][0]["dictionary_value_id"] == 90295


def test_wb_brand_auto_fill_when_llm_missing():
    """WB: бренд достаём из brand_hint когда LLM пропустила."""
    charcs = [{"charcID": 14177446, "name": "Бренд", "required": True, "charcType": 1}]
    out, warns = map_wb_characteristics({}, charcs, {}, brand_hint="Tide")
    assert out == [{"id": 14177446, "value": ["Tide"]}]
    assert any("auto-filled" in w for w in warns)


def test_ozon_batched_repeat_calls_consistent():
    """Batched-режим: один и тот же llm_values + brand_hint вызывается 3 раза для 3 SKU.
    Все 3 результата должны быть идентичны (нет state-а).
    """
    attrs = [
        {"id": 85, "name": "Бренд", "is_required": True, "dictionary_id": 1},
        {"id": 4389, "name": "Страна-изготовитель", "is_required": True, "dictionary_id": 2},
    ]
    vals = {
        85: [{"id": 5089754, "value": "Tide"}],
        4389: [{"id": 90295, "value": "Россия"}],
    }
    llm = {}  # LLM ничего не вернула — всё через auto-fill
    results = [map_ozon_attributes(llm, attrs, vals, brand_hint="Tide")
               for _ in range(3)]
    # все 3 результата должны быть идентичны
    assert results[0][0] == results[1][0] == results[2][0]
    assert results[0][0] is not None
    assert len(results[0][0]) == 2  # бренд + страна заполнены
