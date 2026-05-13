from app.parsing import parse_product_text


def test_parse_key_value_text():
    product = parse_product_text(
        2,
        """
        артикул: 59031
        бренд: Tide
        название: Стиральный порошок Альпийская свежесть 400 г
        доп: габариты 10x6x20, вес 400 г
        """,
    )
    assert product.photo_index == 2
    assert product.sku == "59031"
    assert product.brand == "Tide"
    assert "Стиральный порошок" in product.name


def test_parse_pipe_text():
    product = parse_product_text(1, "ABC-1 | Synergetic | Детское твердое мыло 90 г")
    assert product.sku == "ABC-1"
    assert product.brand == "Synergetic"
    assert product.name == "Детское твердое мыло 90 г"


def test_parse_brand_dash_from_name():
    product = parse_product_text(1, "59031 Tide - Стиральный порошок 400 г")
    assert product.sku == "59031"
    assert product.brand == "Tide"
    assert product.name == "Стиральный порошок 400 г"

