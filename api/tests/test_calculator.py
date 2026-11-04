import json
import os

import pytest

from lia_api.ingest import load_store


def _tool():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        pytest.fail("DATABASE_URL must point at a real PostgreSQL database")
    load_store(database_url)
    from lia_api.calculator import build_calculator_tool

    return build_calculator_tool(database_url)


def _calculate(**kwargs: object) -> dict[str, object]:
    payload = _tool().invoke(kwargs)
    assert isinstance(payload, str)
    return json.loads(payload)


def _counts(result: dict[str, object]) -> list[int]:
    installments = result["installments"]
    assert isinstance(installments, list)
    return [item["count"] for item in installments]


def test_pix_is_five_percent_off_the_list_price_without_an_active_promotion() -> None:
    result = _calculate(name="Shelby SU-21S")

    assert result["name"] == "Shelby SU-21S Soprano Sunburst"
    assert result["list_price"] == "159.90"
    assert result["active_promotion"] is None
    assert result["pix_price"] == "151.91"
    assert "@" not in json.dumps(result)


def test_inactive_promotion_does_not_block_the_pix_discount() -> None:
    result = _calculate(name="Crafter CE-15")

    assert result["name"] == "Crafter CE-15 Elétrico Nylon Natural"
    assert result["list_price"] == "3899.00"
    assert result["active_promotion"] is None
    assert result["pix_price"] == "3704.05"
    assert "Black Friday" not in json.dumps(result)


def test_pix_equals_the_promotional_price_and_does_not_stack() -> None:
    result = _calculate(name="Kalani KAL-700T")

    assert result["list_price"] == "359.00"
    assert result["active_promotion"] == {
        "discount_percent": "10",
        "promotional_price": "323.10",
    }
    assert result["pix_price"] == "323.10"
    assert {"count": 1, "amount": "323.10"} in result["installments"]
    assert {"count": 4, "amount": "80.78"} in result["installments"]
    assert _counts(result) == [1, 2, 3, 4]


def test_cheap_product_keeps_only_the_plans_that_clear_each_minimum() -> None:
    result = _calculate(name="Shelby SU-21S")

    assert result["installments"] == [
        {"count": 1, "amount": "159.90"},
        {"count": 2, "amount": "79.95"},
        {"count": 3, "amount": "53.30"},
    ]


def test_installment_minimums_include_the_exact_tier_and_exclude_the_cent_below() -> None:
    two = _calculate(order_total="100.00")
    assert {"count": 2, "amount": "50.00"} in two["installments"]
    assert _counts(two) == [1, 2]

    below_two = _calculate(order_total="99.98")
    assert below_two["installments"] == [{"count": 1, "amount": "99.98"}]

    three = _calculate(order_total="150.00")
    assert {"count": 3, "amount": "50.00"} in three["installments"]
    assert _counts(three) == [1, 2, 3]

    below_three = _calculate(order_total="149.97")
    assert {"count": 2, "amount": "74.99"} in below_three["installments"]
    assert _counts(below_three) == [1, 2]

    four = _calculate(order_total="320.00")
    assert {"count": 4, "amount": "80.00"} in four["installments"]
    assert _counts(four) == [1, 2, 3, 4]

    below_four = _calculate(order_total="319.96")
    assert {"count": 3, "amount": "106.65"} in below_four["installments"]
    assert _counts(below_four) == [1, 2, 3]

    six = _calculate(order_total="480.00")
    assert {"count": 6, "amount": "80.00"} in six["installments"]
    assert 6 in _counts(six)
    assert 7 not in _counts(six)

    below_six = _calculate(order_total="479.94")
    assert {"count": 4, "amount": "119.99"} in below_six["installments"]
    assert 6 not in _counts(below_six)
    assert 7 not in _counts(below_six)

    seven = _calculate(order_total="700.00")
    assert {"count": 7, "amount": "100.00"} in seven["installments"]
    assert {"count": 6, "amount": "116.67"} in seven["installments"]
    assert _counts(seven) == [1, 2, 3, 4, 5, 6, 7]

    below_seven = _calculate(order_total="699.93")
    assert {"count": 6, "amount": "116.66"} in below_seven["installments"]
    assert 7 not in _counts(below_seven)

    twelve = _calculate(order_total="1200.00")
    assert {"count": 12, "amount": "100.00"} in twelve["installments"]
    assert {"count": 7, "amount": "171.43"} in twelve["installments"]
    assert 12 in _counts(twelve)

    below_twelve = _calculate(order_total="1199.88")
    assert {"count": 11, "amount": "109.08"} in below_twelve["installments"]
    assert {"count": 7, "amount": "171.41"} in below_twelve["installments"]
    assert 12 not in _counts(below_twelve)

    tiny = _calculate(order_total="10.00")
    assert tiny["installments"] == [{"count": 1, "amount": "10.00"}]


def test_campo_grande_charges_35_at_500_and_is_free_only_above_it() -> None:
    exact = _calculate(order_total="500.00", city="Campo Grande")
    assert exact["shipping"] == {
        "city": "Campo Grande",
        "free": False,
        "cost": "35.00",
        "business_days": "1 a 3",
    }

    above = _calculate(order_total="500.01", city="Campo Grande")
    assert above["shipping"]["free"] is True
    assert above["shipping"]["cost"] == "0.00"
    assert above["shipping"]["business_days"] == "1 a 3"

    below = _calculate(order_total="499.99", city="campo grande - MS")
    assert below["shipping"]["free"] is False
    assert below["shipping"]["cost"] == "35.00"

    shelby = _calculate(name="Shelby SU-21S", city="Campo Grande")
    assert shelby["list_price"] == "159.90"
    assert shelby["shipping"]["cost"] == "35.00"
    assert shelby["shipping"]["free"] is False
    assert shelby["shipping"]["business_days"] == "1 a 3"

    yamaha = _calculate(name="Yamaha C40", city="Campo Grande")
    assert yamaha["list_price"] == "599.90"
    assert yamaha["active_promotion"] is None
    assert yamaha["shipping"]["free"] is True
    assert yamaha["shipping"]["cost"] == "0.00"

    ohana = _calculate(name="Ohana CK-20", city="Campo Grande")
    assert ohana["list_price"] == "549.00"
    assert ohana["active_promotion"]["promotional_price"] == "439.20"
    assert ohana["pix_price"] == "439.20"
    assert ohana["shipping"]["free"] is False
    assert ohana["shipping"]["cost"] == "35.00"


def test_shipping_carriers_are_not_read_from_the_policy_pdf(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    (tmp_path / "políticas_da_loja.pdf").write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr("lia_api.calculator.data_dir", lambda: tmp_path)

    with pytest.raises(RuntimeError, match="were not found in the manual"):
        _calculate(order_total="800.00", city="Dourados")


def test_other_cities_are_priced_from_the_cep_with_manual_carriers() -> None:
    result = _calculate(order_total="800.00", city="Dourados")

    shipping = result["shipping"]
    assert shipping["priced_at_checkout"] is True
    assert "CEP" in shipping["message"]
    assert "cost" not in shipping
    assert "35.00" not in json.dumps(shipping)
    assert shipping["carriers"] == [
        {"name": "PAC (Correios)", "timeframe": "5 a 12 dias úteis"},
        {"name": "SEDEX (Correios)", "timeframe": "2 a 5 dias úteis"},
        {"name": "Jadlog (.package)", "timeframe": "3 a 8 dias úteis"},
    ]

    nearby = _calculate(order_total="800.00", city="Sidrolândia")
    assert nearby["shipping"]["priced_at_checkout"] is True
    assert "carriers" in nearby["shipping"]


def test_shipping_without_a_city_does_not_invent_a_cost() -> None:
    result = _calculate(name="Shelby SU-21S")

    assert result["shipping"]["city_required"] is True
    assert "Informe a cidade" in result["shipping"]["message"]
    assert "cost" not in result["shipping"]
    dumped = json.dumps(result["shipping"])
    assert "35.00" not in dumped
    assert "0.00" not in dumped


def test_coming_soon_product_is_not_quoted() -> None:
    result = _calculate(name="Kala KA-ABP-CTG")

    assert result["status"] == "coming_soon"
    assert "pix_price" not in result
    assert "1749" not in json.dumps(result)
