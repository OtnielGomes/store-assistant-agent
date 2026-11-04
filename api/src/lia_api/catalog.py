"""Catalog search over the ingested store database."""

import json
from decimal import ROUND_HALF_UP, Decimal

import psycopg
from langchain_core.tools import tool
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

_PRODUCT_SQL = """
SELECT
    p.product_id,
    p.name,
    c.category_id,
    c.name AS category,
    p.price,
    p.status,
    p.stock_quantity,
    promo.discount_percent,
    (
        SELECT COALESCE(array_agg(pr.description ORDER BY pr.promotion_id), ARRAY[]::text[])
        FROM promotions AS pr
        WHERE pr.product_id = p.product_id
          AND NOT pr.is_active
    ) AS inactive_promotions
FROM products AS p
JOIN categories AS c ON c.category_id = p.category_id
LEFT JOIN LATERAL (
    SELECT discount_percent
    FROM promotions
    WHERE product_id = p.product_id
      AND is_active
    ORDER BY promotion_id
    LIMIT 1
) AS promo ON TRUE
"""


def build_catalog_search_tool(database_url: str):
    """Tool the assistant uses to read catalog Facts. The model never writes SQL."""

    @tool
    def search_catalog(
        name: str | None = None,
        category: str | None = None,
        maximum_price: str | None = None,
        specs: dict[str, str] | None = None,
    ) -> str:
        """Busca produtos do Empório da Música.

        Com name, devolve produtos em qualquer status. Sem name, lista apenas
        produtos ativos com estoque. Filtra por category, maximum_price (preço
        de tabela em reais) e specs (valores exatos, por exemplo strings=nylon).
        Produto descontinuado, em breve ou temporariamente indisponível inclui
        alternatives: produtos ativos com estoque na mesma categoria. Categoria
        sem nenhum produto volta em unstocked_categories. Na busca por nome,
        promoções só inativas voltam em inactive_promotions, sem preço
        promocional. A resposta não traz quantidade em estoque.
        """
        price = Decimal(maximum_price) if maximum_price else None
        result = execute_catalog_search(
            database_url,
            name=name,
            category=category,
            maximum_price=price,
            specs=specs,
        )
        return json.dumps(result, ensure_ascii=False)

    return search_catalog


def execute_catalog_search(
    database_url: str,
    *,
    name: str | None = None,
    category: str | None = None,
    maximum_price: Decimal | None = None,
    specs: dict[str, str] | None = None,
) -> dict[str, object]:
    name_pattern = _contains_pattern(name)
    category_pattern = _contains_pattern(category)
    spec_filter = Jsonb(specs) if specs else None
    params = {
        "name": name_pattern,
        "category": category_pattern,
        "maximum_price": maximum_price,
        "specs": spec_filter,
        "name_lookup": name_pattern is not None,
    }
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        rows = connection.execute(
            f"""
            {_PRODUCT_SQL}
            WHERE (%(name)s::text IS NULL OR p.name ILIKE %(name)s ESCAPE '\\')
              AND (%(category)s::text IS NULL OR c.name ILIKE %(category)s ESCAPE '\\')
              AND (
                    %(maximum_price)s::numeric IS NULL
                    OR p.price <= %(maximum_price)s
              )
              AND (%(specs)s::jsonb IS NULL OR p.specs @> %(specs)s)
              AND (
                    %(name_lookup)s
                    OR (p.status = 'active' AND p.stock_quantity > 0)
              )
            ORDER BY p.name
            """,
            params,
        ).fetchall()
        unstocked: list[str] = []
        if category_pattern is not None:
            unstocked_rows = connection.execute(
                """
                SELECT c.name
                FROM categories AS c
                WHERE c.name ILIKE %(category)s ESCAPE '\\'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM products AS p
                      WHERE p.category_id = c.category_id
                  )
                ORDER BY c.name
                """,
                {"category": category_pattern},
            ).fetchall()
            unstocked = [row["name"] for row in unstocked_rows]
        products = [
            _present(
                row,
                _alternatives(connection, row, maximum_price, spec_filter)
                if name_pattern is not None and not _is_buyable(row)
                else None,
                inactive_promotions=_named_inactive_promotions(row)
                if name_pattern is not None
                else None,
            )
            for row in rows
        ]
    return {"products": products, "unstocked_categories": unstocked}


def _alternatives(
    connection: psycopg.Connection,
    product: dict[str, object],
    maximum_price: Decimal | None,
    specs: Jsonb | None,
) -> list[dict[str, object]]:
    rows = connection.execute(
        f"""
        {_PRODUCT_SQL}
        WHERE p.category_id = %(category_id)s
          AND p.product_id <> %(product_id)s
          AND p.status = 'active'
          AND p.stock_quantity > 0
          AND (%(maximum_price)s::numeric IS NULL OR p.price <= %(maximum_price)s)
          AND (%(specs)s::jsonb IS NULL OR p.specs @> %(specs)s)
        ORDER BY p.name
        """,
        {
            "category_id": product["category_id"],
            "product_id": product["product_id"],
            "maximum_price": maximum_price,
            "specs": specs,
        },
    ).fetchall()
    return [_present(row) for row in rows]


def _present(
    row: dict[str, object],
    alternatives: list[dict[str, object]] | None = None,
    inactive_promotions: list[str] | None = None,
) -> dict:
    product: dict[str, object] = {
        "name": row["name"],
        "category": row["category"],
        "status": row["status"],
        "active_promotion": _promotion(row),
    }
    if inactive_promotions:
        product["inactive_promotions"] = inactive_promotions
    if row["status"] == "active":
        product["availability"] = (
            "available" if int(row["stock_quantity"]) > 0 else "temporarily_unavailable"
        )
    if row["status"] != "coming_soon":
        product["price"] = _money(row["price"])
    if alternatives is not None:
        product["alternatives"] = alternatives
    return product


def _named_inactive_promotions(row: dict[str, object]) -> list[str] | None:
    """Descriptions of inactive promotions when the product has no active one."""
    if row["discount_percent"] is not None or row["status"] == "coming_soon":
        return None
    descriptions = row.get("inactive_promotions") or []
    if not isinstance(descriptions, list):
        return None
    unique: list[str] = []
    for description in descriptions:
        if isinstance(description, str) and description not in unique:
            unique.append(description)
    return unique or None


def _promotion(row: dict[str, object]) -> dict[str, str] | None:
    if row["status"] == "coming_soon" or row["discount_percent"] is None:
        return None
    percent = row["discount_percent"]
    assert isinstance(percent, Decimal)
    price = row["price"]
    assert isinstance(price, Decimal)
    return {
        "discount_percent": _percent(percent),
        "promotional_price": _money(_promotional_price(price, percent)),
    }


def _promotional_price(price: Decimal, discount_percent: Decimal) -> Decimal:
    factor = (Decimal("100") - discount_percent) / Decimal("100")
    return (price * factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _money(value: object) -> str:
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    return f"{amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}"


def _percent(value: Decimal) -> str:
    quantized = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if quantized == quantized.to_integral_value():
        return str(quantized.to_integral_value())
    return f"{quantized:.2f}"


def _is_buyable(row: dict[str, object]) -> bool:
    return row["status"] == "active" and int(row["stock_quantity"]) > 0


def _contains_pattern(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    escaped = value.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
