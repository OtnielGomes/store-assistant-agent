"""Load the store CSVs into PostgreSQL without correcting anomalies."""

import csv
import json
import os
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

DATE_SHIFT_DAYS = 224

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS categories (
        category_id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS products (
        product_id INTEGER PRIMARY KEY,
        price NUMERIC(12, 2) NOT NULL,
        name TEXT NOT NULL,
        category_id INTEGER NOT NULL REFERENCES categories (category_id),
        description TEXT NOT NULL,
        stock_quantity INTEGER NOT NULL CHECK (stock_quantity >= 0),
        status TEXT NOT NULL CHECK (status IN ('active', 'discontinued', 'coming_soon')),
        specs JSONB NOT NULL,
        created_at DATE NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS customers (
        customer_id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        phone TEXT NOT NULL,
        email TEXT NOT NULL,
        city TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS orders (
        order_id INTEGER PRIMARY KEY,
        customer_id INTEGER NOT NULL REFERENCES customers (customer_id),
        order_date DATE NOT NULL,
        status TEXT NOT NULL CHECK (
            status IN ('pending', 'confirmed', 'shipped', 'delivered', 'cancelled')
        ),
        total NUMERIC(12, 2) NOT NULL,
        payment_method TEXT NOT NULL,
        tracking_code TEXT,
        estimated_delivery DATE,
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS order_items (
        order_id INTEGER NOT NULL REFERENCES orders (order_id),
        product_id INTEGER NOT NULL REFERENCES products (product_id),
        quantity INTEGER NOT NULL CHECK (quantity > 0),
        PRIMARY KEY (order_id, product_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS promotions (
        promotion_id INTEGER PRIMARY KEY,
        product_id INTEGER NOT NULL REFERENCES products (product_id),
        discount_percent NUMERIC(5, 2) NOT NULL CHECK (discount_percent >= 0),
        description TEXT NOT NULL,
        is_active BOOLEAN NOT NULL
    )
    """,
)


def data_dir() -> Path:
    """Directory that holds the untouched store CSVs."""
    configured = os.environ.get("DATA_DIR")
    if configured:
        return Path(configured)
    candidate = Path(__file__).resolve().parents[3] / "data"
    if (candidate / "products.csv").is_file():
        return candidate
    return Path.cwd() / "data"


def load_store(database_url: str, source_dir: Path | None = None) -> None:
    """Create the fact tables and upsert every CSV row. Safe to run again."""
    directory = source_dir or data_dir()
    categories = _read_csv(directory / "categories.csv")
    products = _read_csv(directory / "products.csv")
    customers = _read_csv(directory / "customers.csv")
    orders = _read_csv(directory / "orders.csv")
    order_items = _read_csv(directory / "order_items.csv")
    promotions = _read_csv(directory / "promotions.csv")

    with psycopg.connect(database_url) as connection:
        for statement in _SCHEMA:
            connection.execute(statement)
        _upsert_categories(connection, categories)
        _upsert_customers(connection, customers)
        _upsert_products(connection, products)
        _upsert_orders(connection, orders)
        _upsert_order_items(connection, order_items)
        _upsert_promotions(connection, promotions)
        connection.commit()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _blank(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _shift_date(value: str | None) -> date | None:
    raw = _blank(value)
    if raw is None:
        return None
    return date.fromisoformat(raw) + timedelta(days=DATE_SHIFT_DAYS)


def _upsert_categories(connection: psycopg.Connection, rows: list[dict[str, str]]) -> None:
    connection.cursor().executemany(
        """
        INSERT INTO categories (category_id, name, description)
        VALUES (%(category_id)s, %(name)s, %(description)s)
        ON CONFLICT (category_id) DO UPDATE SET
            name = EXCLUDED.name,
            description = EXCLUDED.description
        """,
        [
            {
                "category_id": int(row["category_id"]),
                "name": row["name"],
                "description": row["description"],
            }
            for row in rows
        ],
    )


def _upsert_customers(connection: psycopg.Connection, rows: list[dict[str, str]]) -> None:
    connection.cursor().executemany(
        """
        INSERT INTO customers (customer_id, name, phone, email, city)
        VALUES (%(customer_id)s, %(name)s, %(phone)s, %(email)s, %(city)s)
        ON CONFLICT (customer_id) DO UPDATE SET
            name = EXCLUDED.name,
            phone = EXCLUDED.phone,
            email = EXCLUDED.email,
            city = EXCLUDED.city
        """,
        [
            {
                "customer_id": int(row["customer_id"]),
                "name": row["name"],
                "phone": row["phone"],
                "email": row["email"],
                "city": row["city"],
            }
            for row in rows
        ],
    )


def _upsert_products(connection: psycopg.Connection, rows: list[dict[str, str]]) -> None:
    connection.cursor().executemany(
        """
        INSERT INTO products (
            product_id, price, name, category_id, description,
            stock_quantity, status, specs, created_at
        )
        VALUES (
            %(product_id)s, %(price)s, %(name)s, %(category_id)s, %(description)s,
            %(stock_quantity)s, %(status)s, %(specs)s, %(created_at)s
        )
        ON CONFLICT (product_id) DO UPDATE SET
            price = EXCLUDED.price,
            name = EXCLUDED.name,
            category_id = EXCLUDED.category_id,
            description = EXCLUDED.description,
            stock_quantity = EXCLUDED.stock_quantity,
            status = EXCLUDED.status,
            specs = EXCLUDED.specs,
            created_at = EXCLUDED.created_at
        """,
        [
            {
                "product_id": int(row["product_id"]),
                "price": Decimal(row["price_brl"]),
                "name": row["name"],
                "category_id": int(row["category_id"]),
                "description": row["description"],
                "stock_quantity": int(row["stock_quantity"]),
                "status": row["status"],
                "specs": Jsonb(json.loads(row["specs"])),
                "created_at": _shift_date(row["created_at"]),
            }
            for row in rows
        ],
    )


def _upsert_orders(connection: psycopg.Connection, rows: list[dict[str, str]]) -> None:
    connection.cursor().executemany(
        """
        INSERT INTO orders (
            order_id, customer_id, order_date, status, total, payment_method,
            tracking_code, estimated_delivery, notes
        )
        VALUES (
            %(order_id)s, %(customer_id)s, %(order_date)s, %(status)s, %(total)s,
            %(payment_method)s, %(tracking_code)s, %(estimated_delivery)s, %(notes)s
        )
        ON CONFLICT (order_id) DO UPDATE SET
            customer_id = EXCLUDED.customer_id,
            order_date = EXCLUDED.order_date,
            status = EXCLUDED.status,
            total = EXCLUDED.total,
            payment_method = EXCLUDED.payment_method,
            tracking_code = EXCLUDED.tracking_code,
            estimated_delivery = EXCLUDED.estimated_delivery,
            notes = EXCLUDED.notes
        """,
        [
            {
                "order_id": int(row["order_id"]),
                "customer_id": int(row["customer_id"]),
                "order_date": _shift_date(row["order_date"]),
                "status": row["status"],
                "total": Decimal(row["total_brl"]),
                "payment_method": row["payment_method"],
                "tracking_code": _blank(row["tracking_code"]),
                "estimated_delivery": _shift_date(row["estimated_delivery"]),
                "notes": _blank(row["notes"]),
            }
            for row in rows
        ],
    )


def _upsert_order_items(connection: psycopg.Connection, rows: list[dict[str, str]]) -> None:
    connection.cursor().executemany(
        """
        INSERT INTO order_items (order_id, product_id, quantity)
        VALUES (%(order_id)s, %(product_id)s, %(quantity)s)
        ON CONFLICT (order_id, product_id) DO UPDATE SET
            quantity = EXCLUDED.quantity
        """,
        [
            {
                "order_id": int(row["order_id"]),
                "product_id": int(row["product_id"]),
                "quantity": int(row["quantity"]),
            }
            for row in rows
        ],
    )


def _upsert_promotions(connection: psycopg.Connection, rows: list[dict[str, str]]) -> None:
    connection.cursor().executemany(
        """
        INSERT INTO promotions (
            promotion_id, product_id, discount_percent, description, is_active
        )
        VALUES (
            %(promotion_id)s, %(product_id)s, %(discount_percent)s,
            %(description)s, %(is_active)s
        )
        ON CONFLICT (promotion_id) DO UPDATE SET
            product_id = EXCLUDED.product_id,
            discount_percent = EXCLUDED.discount_percent,
            description = EXCLUDED.description,
            is_active = EXCLUDED.is_active
        """,
        [
            {
                "promotion_id": int(row["promotion_id"]),
                "product_id": int(row["product_id"]),
                "discount_percent": Decimal(row["discount_percent"]),
                "description": row["description"],
                "is_active": row["is_active"] == "1",
            }
            for row in rows
        ],
    )


def main() -> None:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL is required to load the store data.")
    load_store(database_url)


if __name__ == "__main__":
    main()
