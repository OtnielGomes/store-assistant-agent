import json
import os
from uuid import uuid4

import pytest

from lia_api.ingest import load_store
from tests.order_examples import (
    ORDERS,
    STORE_CLOCK,
    assert_no_customer_contact,
    verified_payload,
)

NOT_VERIFIED = {"verified": False, "verification_locked": False, "order": None}
LOCKED = {"verified": False, "verification_locked": True, "order": None}


def _database_url() -> str:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        pytest.fail("DATABASE_URL must point at a real PostgreSQL database")
    load_store(database_url)
    return database_url


def _lookup(order_number: int, phone_last_four: str, conversation_id: str) -> tuple[dict, str]:
    database_url = _database_url()
    from lia_api.orders import build_order_lookup_tool

    tool = build_order_lookup_tool(database_url)
    payload = tool.invoke(
        {"order_number": order_number, "phone_last_four": phone_last_four},
        config={"configurable": {"thread_id": conversation_id, "clock": STORE_CLOCK}},
    )
    assert isinstance(payload, str)
    return json.loads(payload), payload


def test_wrong_digits_hide_the_order_and_correct_digits_return_recorded_facts() -> None:
    example = ORDERS[7]
    conversation_id = f"order-8-{uuid4()}"

    hidden, hidden_raw = _lookup(8, "0000", conversation_id)
    assert hidden == NOT_VERIFIED
    assert_no_customer_contact(hidden_raw, example)

    verified, raw = _lookup(8, "7821", f"order-8-ok-{uuid4()}")
    assert verified == verified_payload(example)
    assert verified["order"]["order_total"] == "349.90"
    assert verified["order"]["order_date"] == "2026-09-27"
    assert verified["order"]["estimated_delivery"] == "2026-10-10"
    assert verified["order"]["anomaly_flags"] == ["overdue_delivery"]
    assert_no_customer_contact(raw, example)


@pytest.mark.parametrize("example", ORDERS, ids=lambda example: f"order-{example['order_number']}")
def test_verified_lookup_returns_recorded_order_without_contact_details(
    example: dict[str, object],
) -> None:
    order_number = example["order_number"]
    phone_last_four = example["phone_last_four"]
    assert isinstance(order_number, int)
    assert isinstance(phone_last_four, str)

    result, raw = _lookup(order_number, phone_last_four, f"order-{order_number}-{uuid4()}")

    assert result == verified_payload(example)
    assert_no_customer_contact(raw, example)


def test_full_phone_number_does_not_verify_the_order() -> None:
    result, raw = _lookup(8, "67996547821", f"full-phone-{uuid4()}")

    assert result == NOT_VERIFIED
    assert_no_customer_contact(raw, ORDERS[7])


def test_leading_zero_in_the_last_four_digits_still_verifies() -> None:
    example = ORDERS[6]
    result, raw = _lookup(7, "0123", f"leading-zero-{uuid4()}")

    assert result == verified_payload(example)
    assert result["order"]["customer_first_name"] == "Letícia"
    assert_no_customer_contact(raw, example)


def test_three_failed_verifications_lock_later_lookups_for_that_conversation() -> None:
    conversation_id = f"lock-{uuid4()}"
    other_conversation = f"lock-other-{uuid4()}"

    first, _ = _lookup(8, "0000", conversation_id)
    second, _ = _lookup(8, "1111", conversation_id)
    third, third_raw = _lookup(8, "2222", conversation_id)
    fourth, fourth_raw = _lookup(8, "7821", conversation_id)
    other, other_raw = _lookup(8, "7821", other_conversation)

    assert first == NOT_VERIFIED
    assert second == NOT_VERIFIED
    assert third == LOCKED
    assert fourth == LOCKED
    assert_no_customer_contact(third_raw, ORDERS[7])
    assert_no_customer_contact(fourth_raw, ORDERS[7])
    assert "Ana" not in fourth_raw
    assert "349.90" not in fourth_raw
    assert other == verified_payload(ORDERS[7])
    assert_no_customer_contact(other_raw, ORDERS[7])


def test_failures_accumulate_across_a_successful_lookup() -> None:
    conversation_id = f"accumulate-{uuid4()}"

    assert _lookup(8, "0000", conversation_id)[0] == NOT_VERIFIED
    assert _lookup(8, "1111", conversation_id)[0] == NOT_VERIFIED
    assert _lookup(8, "7821", conversation_id)[0] == verified_payload(ORDERS[7])
    locked, raw = _lookup(8, "2222", conversation_id)

    assert locked == LOCKED
    assert_no_customer_contact(raw, ORDERS[7])
