import json
import os
from uuid import uuid4

import pytest

from lia_api.ingest import load_store
from tests.order_examples import ORDERS, STORE_CLOCK, assert_no_customer_contact

# Deadlines are known literals for the frozen clock 2026-11-03.
# Order 7 was delivered on 2026-09-29. Order 4 was delivered on 2026-08-03.
ORDER_7 = {
    "verified": True,
    "verification_locked": False,
    "status": "delivered",
    "receipt_date": "2026-09-29",
    "deadlines": {
        "withdrawal_and_preference_exchange": {
            "days": 7,
            "deadline": "2026-10-06",
            "deadline_day_month": "06/10",
            "open": False,
        },
        "defect_exchange": {
            "days": 30,
            "deadline": "2026-10-29",
            "deadline_day_month": "29/10",
            "open": False,
        },
        "legal_warranty": {
            "days": 90,
            "deadline": "2026-12-28",
            "deadline_day_month": "28/12",
            "open": True,
        },
    },
}

ORDER_4 = {
    "verified": True,
    "verification_locked": False,
    "status": "delivered",
    "receipt_date": "2026-08-03",
    "deadlines": {
        "withdrawal_and_preference_exchange": {
            "days": 7,
            "deadline": "2026-08-10",
            "deadline_day_month": "10/08",
            "open": False,
        },
        "defect_exchange": {
            "days": 30,
            "deadline": "2026-09-02",
            "deadline_day_month": "02/09",
            "open": False,
        },
        "legal_warranty": {
            "days": 90,
            "deadline": "2026-11-01",
            "deadline_day_month": "01/11",
            "open": False,
        },
    },
}


def _database_url() -> str:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        pytest.fail("DATABASE_URL must point at a real PostgreSQL database")
    load_store(database_url)
    return database_url


def _check(order_number: int, phone_last_four: str) -> tuple[dict, str]:
    database_url = _database_url()
    from lia_api.returns import build_return_window_tool

    tool = build_return_window_tool(database_url)
    payload = tool.invoke(
        {"order_number": order_number, "phone_last_four": phone_last_four},
        config={"configurable": {"thread_id": f"returns-{uuid4()}", "clock": STORE_CLOCK}},
    )
    assert isinstance(payload, str)
    return json.loads(payload), payload


def test_order_7_is_past_seven_and_thirty_days_but_within_ninety() -> None:
    example = ORDERS[6]
    hidden, hidden_raw = _check(7, "0000")
    assert hidden == {"verified": False, "verification_locked": False, "order": None}
    assert_no_customer_contact(hidden_raw, example)

    result, raw = _check(7, "0123")

    assert result == ORDER_7
    assert result["deadlines"]["withdrawal_and_preference_exchange"]["open"] is False
    assert result["deadlines"]["defect_exchange"]["open"] is False
    assert result["deadlines"]["legal_warranty"]["open"] is True
    assert_no_customer_contact(raw, example)


def test_undelivered_order_has_no_return_deadlines() -> None:
    example = ORDERS[8]
    assert example["status"] != "delivered"
    result, raw = _check(9, "5678")

    assert result == {
        "verified": True,
        "verification_locked": False,
        "status": "confirmed",
        "receipt_date": None,
        "deadlines": None,
    }
    assert "2026-" not in raw
    assert_no_customer_contact(raw, example)


def test_order_4_is_past_ninety_days() -> None:
    example = ORDERS[3]
    result, raw = _check(4, "3456")

    assert result == ORDER_4
    assert result["deadlines"]["legal_warranty"]["open"] is False
    assert_no_customer_contact(raw, example)
