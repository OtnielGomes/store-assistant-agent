import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_ingest_loads_facts_with_the_fixed_date_shift_and_is_idempotent() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        pytest.fail("DATABASE_URL must point at a real PostgreSQL database")

    from lia_api.ingest import load_store

    load_store(database_url)
    before = _snapshot(database_url)
    load_store(database_url)
    after = _snapshot(database_url)

    assert after == before
    assert before["counts"] == {
        "categories": 9,
        "products": 65,
        "customers": 50,
        "orders": 20,
        "order_items": 22,
        "promotions": 25,
    }
    assert before["order_16_date"] == date(2026, 11, 1)
    assert before["order_16_estimated_delivery"] is None
    assert before["order_1_estimated_delivery"] == date(2026, 6, 6)
    assert before["order_8_estimated_delivery"] == date(2026, 10, 10)
    assert before["order_3_total"] == Decimal("3450.00")
    assert before["product_81_created_at"] == date(2021, 10, 25)
    assert before["product_81_price"] == Decimal("599.90")
    assert before["product_136_stock"] == 13
    assert before["product_136_status"] == "discontinued"
    assert before["product_130_status"] == "coming_soon"
    assert before["product_130_price"] == Decimal("1749.00")
    assert before["product_96_stock"] == 0
    assert before["product_96_status"] == "active"
    assert before["promotion_1_active"] is False
    assert before["promotion_19_active"] is True
    assert "numeric" in before["column_types"]["products.price"]
    assert before["column_types"]["products.specs"] == "jsonb"
    assert before["column_types"]["orders.total"] == "numeric"
    assert before["column_types"]["promotions.is_active"] == "boolean"
    assert "active" in before["product_status_check"]
    assert "discontinued" in before["product_status_check"]
    assert "coming_soon" in before["product_status_check"]
    assert "pending" in before["order_status_check"]
    assert "cancelled" in before["order_status_check"]
    assert before["foreign_keys"] >= {
        ("products", "categories"),
        ("orders", "customers"),
        ("order_items", "orders"),
        ("order_items", "products"),
        ("promotions", "products"),
    }
    raw_order_16 = (REPO_ROOT / "data" / "orders.csv").read_text(encoding="utf-8")
    assert "16,10,2026-03-22,pending,429.9,credit_6x" in raw_order_16


def _snapshot(database_url: str) -> dict[str, object]:
    with psycopg.connect(database_url) as connection:
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "categories",
                "products",
                "customers",
                "orders",
                "order_items",
                "promotions",
            )
        }
        order_16 = connection.execute(
            "SELECT order_date, estimated_delivery FROM orders WHERE order_id = 16"
        ).fetchone()
        order_1_delivery = connection.execute(
            "SELECT estimated_delivery FROM orders WHERE order_id = 1"
        ).fetchone()[0]
        order_8_delivery = connection.execute(
            "SELECT estimated_delivery FROM orders WHERE order_id = 8"
        ).fetchone()[0]
        order_3_total = connection.execute(
            "SELECT total FROM orders WHERE order_id = 3"
        ).fetchone()[0]
        product_81 = connection.execute(
            "SELECT created_at, price FROM products WHERE product_id = 81"
        ).fetchone()
        product_136 = connection.execute(
            "SELECT stock_quantity, status FROM products WHERE product_id = 136"
        ).fetchone()
        product_130 = connection.execute(
            "SELECT status, price FROM products WHERE product_id = 130"
        ).fetchone()
        product_96 = connection.execute(
            "SELECT stock_quantity, status FROM products WHERE product_id = 96"
        ).fetchone()
        promotion_1 = connection.execute(
            "SELECT is_active FROM promotions WHERE promotion_id = 1"
        ).fetchone()[0]
        promotion_19 = connection.execute(
            "SELECT is_active FROM promotions WHERE promotion_id = 19"
        ).fetchone()[0]
        column_types = {
            f"{row[0]}.{row[1]}": row[2]
            for row in connection.execute(
                """
                SELECT table_name, column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND (table_name, column_name) IN (
                    ('products', 'price'),
                    ('products', 'specs'),
                    ('orders', 'total'),
                    ('promotions', 'is_active')
                  )
                """
            ).fetchall()
        }
        product_status_check = " ".join(
            row[0]
            for row in connection.execute(
                """
                SELECT pg_get_constraintdef(oid)
                FROM pg_constraint
                WHERE conrelid = 'products'::regclass AND contype = 'c'
                """
            ).fetchall()
        )
        order_status_check = " ".join(
            row[0]
            for row in connection.execute(
                """
                SELECT pg_get_constraintdef(oid)
                FROM pg_constraint
                WHERE conrelid = 'orders'::regclass AND contype = 'c'
                """
            ).fetchall()
        )
        foreign_keys = {
            (row[0], row[1])
            for row in connection.execute(
                """
                SELECT child.relname, parent.relname
                FROM pg_constraint constraint_row
                JOIN pg_class child ON child.oid = constraint_row.conrelid
                JOIN pg_class parent ON parent.oid = constraint_row.confrelid
                WHERE constraint_row.contype = 'f'
                """
            ).fetchall()
        }
    return {
        "counts": counts,
        "order_16_date": order_16[0],
        "order_16_estimated_delivery": order_16[1],
        "order_1_estimated_delivery": order_1_delivery,
        "order_8_estimated_delivery": order_8_delivery,
        "order_3_total": order_3_total,
        "product_81_created_at": product_81[0],
        "product_81_price": product_81[1],
        "product_136_stock": product_136[0],
        "product_136_status": product_136[1],
        "product_130_status": product_130[0],
        "product_130_price": product_130[1],
        "product_96_stock": product_96[0],
        "product_96_status": product_96[1],
        "promotion_1_active": promotion_1,
        "promotion_19_active": promotion_19,
        "column_types": column_types,
        "product_status_check": product_status_check,
        "order_status_check": order_status_check,
        "foreign_keys": foreign_keys,
    }
