"""Тесты persistent cache юзерских решений."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import decision_cache


@pytest.fixture
def tmp_cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Изолированная директория для cache на каждый тест."""
    monkeypatch.setenv("CZ_DECISIONS_DIR", str(tmp_path))
    return tmp_path


def test_significant_words_basic():
    out = decision_cache._significant_words("Стиральный порошок Альпийская свежесть 400 г", n=3)
    assert out == ["стиральный", "порошок", "альпийская"]


def test_significant_words_drops_stopwords_and_numbers():
    out = decision_cache._significant_words("Гель для душа без парабенов 250 мл", n=3)
    assert "для" not in out
    assert "250" not in out
    assert "гель" in out


def test_make_key_normalizes():
    b, n = decision_cache._make_key("  TIDE ", "Стиральный порошок 400 г")
    assert b == "tide"
    assert n == "стиральный порошок"


def test_append_and_read_roundtrip(tmp_cache_dir):
    decision_cache.append_cache(
        "profit", "ozon", "Tide",
        "Tide Стиральный порошок Альпийская свежесть 400 г",
        {"Цвет": "Белый", "ТНВЭД": "3402901000"},
    )
    answers = decision_cache.read_cached_answers(
        "profit", "ozon", "Tide", "Стиральный порошок Альпийская свежесть",
    )
    assert answers == {"Цвет": "Белый", "ТНВЭД": "3402901000"}


def test_later_records_override_earlier(tmp_cache_dir):
    decision_cache.append_cache("profit", "wb", "Tide", "Стиральный порошок",
                                {"Цвет": "Белый"})
    decision_cache.append_cache("profit", "wb", "Tide", "Стиральный порошок",
                                {"Цвет": "Зелёный", "Объём": "400"})
    answers = decision_cache.read_cached_answers(
        "profit", "wb", "Tide", "Стиральный порошок",
    )
    assert answers["Цвет"] == "Зелёный"
    assert answers["Объём"] == "400"


def test_different_brand_isolated(tmp_cache_dir):
    decision_cache.append_cache("profit", "ozon", "Tide", "Стиральный порошок",
                                {"Цвет": "Белый"})
    decision_cache.append_cache("profit", "ozon", "Persil", "Стиральный порошок",
                                {"Цвет": "Жёлтый"})
    a_tide = decision_cache.read_cached_answers("profit", "ozon", "Tide",
                                                "Стиральный порошок")
    a_persil = decision_cache.read_cached_answers("profit", "ozon", "Persil",
                                                  "Стиральный порошок")
    assert a_tide["Цвет"] == "Белый"
    assert a_persil["Цвет"] == "Жёлтый"


def test_different_marketplace_isolated(tmp_cache_dir):
    decision_cache.append_cache("profit", "ozon", "Tide", "Стиральный порошок",
                                {"Бренд": "Tide"})
    decision_cache.append_cache("profit", "wb", "Tide", "Стиральный порошок",
                                {"Бренд": "TideWB"})
    o = decision_cache.read_cached_answers("profit", "ozon", "Tide",
                                           "Стиральный порошок")
    w = decision_cache.read_cached_answers("profit", "wb", "Tide",
                                           "Стиральный порошок")
    assert o == {"Бренд": "Tide"}
    assert w == {"Бренд": "TideWB"}


def test_empty_answers_not_written(tmp_cache_dir):
    decision_cache.append_cache("profit", "ozon", "Tide", "Стир порошок", {})
    decision_cache.append_cache("profit", "ozon", "Tide", "Стир порошок",
                                {"Цвет": "", "ТНВЭД": None})
    a = decision_cache.read_cached_answers("profit", "ozon", "Tide", "Стир порошок")
    assert a == {}


def test_missing_brand_or_name_returns_empty(tmp_cache_dir):
    decision_cache.append_cache("p", "o", "", "name", {"x": "y"})
    decision_cache.append_cache("p", "o", "Tide", "", {"x": "y"})
    assert decision_cache.read_cached_answers("p", "o", "", "name") == {}
    assert decision_cache.read_cached_answers("p", "o", "Tide", "") == {}


def test_cache_file_path_layout(tmp_cache_dir):
    decision_cache.append_cache("kabinet1", "ozon", "Tide", "Стир порошок",
                                {"X": "Y"})
    expected = tmp_cache_dir / "kabinet1" / "ozon.jsonl"
    assert expected.exists()
    content = expected.read_text(encoding="utf-8")
    assert '"X": "Y"' in content


def test_cabinet_default_when_empty(tmp_cache_dir):
    decision_cache.append_cache("", "ozon", "Tide", "Стир порошок",
                                {"X": "Y"})
    assert (tmp_cache_dir / "default" / "ozon.jsonl").exists()
