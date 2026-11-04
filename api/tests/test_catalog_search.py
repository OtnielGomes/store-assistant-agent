import json
import os

import pytest

from lia_api.ingest import load_store


def _tool():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        pytest.fail("DATABASE_URL must point at a real PostgreSQL database")
    load_store(database_url)
    from lia_api.catalog import build_catalog_search_tool

    return build_catalog_search_tool(database_url)


def _search(**kwargs: object) -> dict[str, object]:
    payload = _tool().invoke(kwargs)
    assert isinstance(payload, str)
    return json.loads(payload)


def test_name_lookup_returns_discontinued_product_without_a_stock_count() -> None:
    result = _search(name="Ibanez Bass 2X")

    assert result["unstocked_categories"] == []
    products = result["products"]
    assert len(products) == 1
    product = products[0]
    assert product["name"] == "Ibanez Bass 2X Electric Bass"
    assert product["category"] == "Baixos"
    assert product["status"] == "discontinued"
    assert product["price"] == "441.00"
    assert product["active_promotion"] is None
    assert "availability" not in product
    assert "stock_quantity" not in product
    assert "13" not in json.dumps(product)
    names = {item["name"] for item in product["alternatives"]}
    assert names == {
        "Music Man Bass 1X Electric Bass",
        "Yamaha Bass 3X Electric Bass",
        "Music Man Bass 4X Electric Bass",
    }
    assert all(item["availability"] == "available" for item in product["alternatives"])
    assert all(item["status"] == "active" for item in product["alternatives"])
    assert all("stock_quantity" not in item for item in product["alternatives"])


def test_name_lookup_marks_product_96_temporarily_unavailable() -> None:
    result = _search(name="Giannini GF-3D")

    product = result["products"][0]
    assert product["name"] == "Giannini GF-3D Dreadnought Sunburst"
    assert product["status"] == "active"
    assert product["availability"] == "temporarily_unavailable"
    assert product["price"] == "799.90"
    assert product["category"] == "Violões"
    assert product["active_promotion"] is None
    assert "stock_quantity" not in product
    alternative_names = {item["name"] for item in product["alternatives"]}
    assert "Yamaha C40 Nylon Natural" in alternative_names
    assert "Giannini GF-3D Dreadnought Sunburst" not in alternative_names
    assert "Shelby SN-7C 7 Cordas Nylon Natural" not in alternative_names
    assert all(item["availability"] == "available" for item in product["alternatives"])


def test_coming_soon_product_hides_its_price_and_offers_alternatives() -> None:
    result = _search(name="Kala KA-ABP-CTG")

    product = result["products"][0]
    assert product["name"] == "Kala KA-ABP-CTG Barítono Cedar"
    assert product["status"] == "coming_soon"
    assert product["category"] == "Ukuleles"
    assert "price" not in product
    assert product["active_promotion"] is None
    assert "1749" not in json.dumps(result)
    alternative_names = {item["name"] for item in product["alternatives"]}
    assert "Ohana CK-20 Concert Natural" in alternative_names
    assert "Kalani KAL-700T Tenor Natural" in alternative_names
    assert "Kala KA-ABP-CTG Barítono Cedar" not in alternative_names


def test_active_promotions_include_percent_and_promotional_price() -> None:
    expected = {
        "Crafter HT-100 Folk Aço Natural": ("2399.00", "18", "1967.18"),
        "Taylor 110e Dreadnought Natural": ("5999.00", "8", "5519.08"),
        "Ohana CK-20 Concert Natural": ("549.00", "20", "439.20"),
        "Kalani KAL-700T Tenor Natural": ("359.00", "10", "323.10"),
    }
    for name, (price, percent, promotional) in expected.items():
        product = _search(name=name)["products"][0]
        assert product["name"] == name
        assert product["price"] == price
        assert product["availability"] == "available"
        assert product["status"] == "active"
        assert product["active_promotion"] == {
            "discount_percent": percent,
            "promotional_price": promotional,
        }
        assert "alternatives" not in product


def test_inactive_promotions_are_not_offers() -> None:
    product = _search(name="Crafter CE-15")["products"][0]

    assert product["name"] == "Crafter CE-15 Elétrico Nylon Natural"
    assert product["price"] == "3899.00"
    assert product["active_promotion"] is None
    assert "Black Friday" not in json.dumps(product)

    multiple = _search(name="Martin D-28 Dreadnought Natural")["products"][0]
    assert multiple["active_promotion"] is None


def test_listing_keeps_only_active_products_with_stock() -> None:
    result = _search(category="Violões")
    names = {product["name"] for product in result["products"]}

    assert result["unstocked_categories"] == []
    assert "Yamaha C40 Nylon Natural" in names
    assert "Giannini GF-3D Dreadnought Sunburst" not in names
    assert "Shelby SN-7C 7 Cordas Nylon Natural" not in names
    assert all(product["status"] == "active" for product in result["products"])
    assert all(product["availability"] == "available" for product in result["products"])
    assert all("alternatives" not in product for product in result["products"])


def test_filters_by_maximum_price_and_spec_values() -> None:
    priced = _search(category="Ukuleles", maximum_price="400")
    priced_names = {product["name"] for product in priced["products"]}
    assert "Kalani KAL-700T Tenor Natural" in priced_names
    assert "Ohana CK-20 Concert Natural" not in priced_names
    assert "Kala KA-ABP-CTG Barítono Cedar" not in priced_names

    steel = _search(category="Violões", maximum_price="600", specs={"strings": "steel"})
    steel_names = {product["name"] for product in steel["products"]}
    assert "Shelby SN-100 Folk Aço Preto" in steel_names
    assert "Yamaha C40 Nylon Natural" not in steel_names
    assert "Giannini GF-3D Dreadnought Sunburst" not in steel_names
    assert "Crafter HT-100 Folk Aço Natural" not in steel_names


def test_categories_without_products_are_unstocked_not_discontinued() -> None:
    wind = _search(category="Instrumentos de Sopro")
    assert wind["products"] == []
    assert wind["unstocked_categories"] == [
        "Instrumentos de Sopro (Madeiras)",
        "Instrumentos de Sopro (Metais)",
    ]
    assert "discontinued" not in json.dumps(wind)

    strings = _search(category="Cordas Orquestrais")
    assert strings["products"] == []
    assert strings["unstocked_categories"] == ["Cordas Orquestrais"]
    assert "discontinued" not in json.dumps(strings)

    missing = _search(name="saxofone")
    assert missing["products"] == []
    assert missing["unstocked_categories"] == []
