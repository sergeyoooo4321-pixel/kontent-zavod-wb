"""Юнит-тесты бизнес-правил §5.2 ТЗ."""
from app.rules import (
    add_cm_to_dims,
    expand_to_3_skus,
    join_multivalue,
    limit_chars,
    nds_value,
    pack_dims,
    pick_from_dict,
    round_to_hundred,
    round_up,
    round_up_2dp,
    strip_brand,
)


def test_round_up():
    assert round_up(0.1) == 1
    assert round_up(5.0) == 5
    assert round_up(5.001) == 6


def test_round_to_hundred():
    assert round_to_hundred(97) == 100
    assert round_to_hundred(343) == 350
    assert round_to_hundred(7) == 10
    assert round_to_hundred(1) == 5
    assert round_to_hundred(1500) == 1500


def test_round_up_2dp():
    assert round_up_2dp(0.1234) == 0.13
    assert round_up_2dp(0.50) == 0.50


def test_add_cm_to_dims():
    assert add_cm_to_dims({"l": 10, "w": 5, "h": 3}) == {"l": 11, "w": 6, "h": 4}


def test_pack_dims_x1():
    assert pack_dims({"l": 10, "w": 5, "h": 3}, 1) == {"l": 10, "w": 5, "h": 3}


def test_pack_dims_x2_smallest_doubled():
    # меньшая h=3, h*2=6, остальные как у одиночки
    assert pack_dims({"l": 10, "w": 5, "h": 3}, 2) == {"l": 10, "w": 5, "h": 6}


def test_pack_dims_x3():
    assert pack_dims({"l": 10, "w": 5, "h": 3}, 3) == {"l": 10, "w": 5, "h": 9}


def test_expand_to_3_skus_basic():
    out = expand_to_3_skus({
        "sku": "A",
        "name": "Чай",
        "weight": 100,
        "dims": {"l": 10, "w": 5, "h": 3},
    })
    assert len(out) == 3
    assert [r["sku"] for r in out] == ["A", "Ax2", "Ax3"]
    assert [r["qty"] for r in out] == [1, 2, 3]
    # Веса
    assert out[0]["weight_unit_g"] == 100
    assert out[0]["weight_packed_g"] == 100
    assert out[1]["weight_packed_g"] == 200
    assert out[2]["weight_packed_g"] == 300
    # WB кг
    assert out[0]["weight_wb_kg"] == 0.1
    assert out[2]["weight_wb_kg"] == 0.3


def test_expand_with_internet_dims():
    out = expand_to_3_skus(
        {"sku": "A", "name": "X", "weight": 50, "dims": {"l": 10, "w": 5, "h": 3}},
        dims_from_internet=True,
    )
    # +1 см к одиночке
    assert out[0]["dims"] == {"l": 11, "w": 6, "h": 4}
    # для x2: меньшая (h=4 после +1) × 2 = 8
    assert out[1]["dims"]["h"] == 8


def test_strip_brand():
    assert strip_brand("Lorem Ipsum Brand X", "Lorem") == "Ipsum Brand X"
    assert strip_brand("Brand", None) == "Brand"


def test_limit_chars():
    assert limit_chars("Hello", 10) == "Hello"
    assert limit_chars("Hello world", 7) == "Hello…"
    assert limit_chars("a" * 100, 60).endswith("…")
    assert len(limit_chars("a" * 100, 60)) == 60


def test_nds_value():
    assert nds_value() == 22


def test_join_multivalue():
    assert join_multivalue(["a", "b", "c"]) == "a;b;c"
    assert join_multivalue(["a", "", "b"]) == "a;b"
    assert join_multivalue([" a ", "b"]) == "a;b"


def test_pick_from_dict_exact():
    v, sub = pick_from_dict(["Красный", "Синий"], "Красный")
    assert v == "Красный"
    assert sub is False


def test_pick_from_dict_case_insensitive():
    v, sub = pick_from_dict(["Красный", "Синий"], "красный")
    assert v == "Красный"
    assert sub is False


def test_pick_from_dict_nearest():
    v, sub = pick_from_dict(["Красный", "Синий"], "Красноватый")
    assert v == "Красный"
    assert sub is True


def test_pick_from_dict_empty():
    v, sub = pick_from_dict([], "anything")
    assert v is None
    assert sub is True
