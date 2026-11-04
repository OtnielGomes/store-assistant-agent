"""Price, PIX, installment, and shipping quotes. All arithmetic stays in code."""

import json
import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import psycopg
from langchain_core.tools import tool
from psycopg.rows import dict_row
from pypdf import PdfReader

from lia_api.ingest import data_dir

_CENT = Decimal("0.01")
_FREE_SHIPPING_ABOVE = Decimal("500.00")
_CAMPO_GRANDE_FEE = Decimal("35.00")
_PIX_FACTOR = Decimal("0.95")
_CARRIER_PATTERN = re.compile(
    r"(PAC(?:\s*\([^)]+\))?|SEDEX(?:\s*\([^)]+\))?|Jadlog(?:\s*\([^)]+\))?)"
    r"\s*\|?\s+"
    r"(\d+\s+a\s+\d+\s+dias\s+úteis)",
    re.IGNORECASE,
)


def build_calculator_tool(database_url: str):
    """Tool the assistant uses for PIX, installments, and shipping."""

    @tool
    def calculate_price(
        name: str | None = None,
        city: str | None = None,
        order_total: str | None = None,
    ) -> str:
        """Calcula preço promocional, PIX, parcelas e frete.

        Com name, lê o produto no catálogo. O PIX é 5% sobre o preço de tabela
        só sem promoção ativa; com promoção ativa, o PIX é o preço promocional
        e não acumula. Parcelas usam o preço promocional quando existe: 1x
        sempre; 2x e 3x com parcela de pelo menos R$ 50; 4x a 6x de pelo menos
        R$ 80; 7x a 12x de pelo menos R$ 100. Frete da cidade de Campo Grande é
        grátis acima de R$ 500 e R$ 35 nos outros valores, em 1 a 3 dias úteis.
        Para outra cidade, o valor sai pelo CEP na finalização. Sem city, o
        frete não é calculado. order_total serve quando o cliente informa o
        valor do pedido e não um produto.
        """
        result = execute_calculation(
            database_url,
            name=name,
            city=city,
            order_total=Decimal(order_total) if order_total else None,
        )
        return json.dumps(result, ensure_ascii=False)

    return calculate_price


def execute_calculation(
    database_url: str,
    *,
    name: str | None = None,
    city: str | None = None,
    order_total: Decimal | None = None,
) -> dict[str, object]:
    if name and name.strip():
        return _quote_named_product(database_url, name.strip(), city)
    if order_total is not None:
        return _quote_amount(order_total, discount_percent=None, city=city)
    return {"error": "Informe o produto ou o valor do pedido."}


def _quote_named_product(
    database_url: str,
    name: str,
    city: str | None,
) -> dict[str, object]:
    rows = _lookup_products(database_url, name)
    if len(rows) != 1:
        return {"products": [row["name"] for row in rows]}
    row = rows[0]
    if row["status"] == "coming_soon":
        return {
            "name": row["name"],
            "status": "coming_soon",
            "active_promotion": None,
        }
    price = row["price"]
    assert isinstance(price, Decimal)
    discount = row["discount_percent"]
    if discount is not None:
        assert isinstance(discount, Decimal)
    quote = _quote_amount(price, discount_percent=discount, city=city)
    quote["name"] = row["name"]
    quote["status"] = row["status"]
    return quote


def _lookup_products(database_url: str, name: str) -> list[dict[str, object]]:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            SELECT
                p.name,
                p.price,
                p.status,
                promo.discount_percent
            FROM products AS p
            LEFT JOIN LATERAL (
                SELECT discount_percent
                FROM promotions
                WHERE product_id = p.product_id
                  AND is_active
                ORDER BY promotion_id
                LIMIT 1
            ) AS promo ON TRUE
            WHERE p.name ILIKE %(name)s ESCAPE '\\'
            ORDER BY p.name
            """,
            {"name": _contains_pattern(name)},
        ).fetchall()
    return rows


def _quote_amount(
    list_price: Decimal,
    *,
    discount_percent: Decimal | None,
    city: str | None,
) -> dict[str, object]:
    if discount_percent is None:
        payable = list_price
        pix = (list_price * _PIX_FACTOR).quantize(_CENT, rounding=ROUND_HALF_UP)
        promotion = None
    else:
        factor = (Decimal("100") - discount_percent) / Decimal("100")
        payable = (list_price * factor).quantize(_CENT, rounding=ROUND_HALF_UP)
        pix = payable
        promotion = {
            "discount_percent": _percent(discount_percent),
            "promotional_price": _money(payable),
        }
    return {
        "list_price": _money(list_price),
        "active_promotion": promotion,
        "pix_price": _money(pix),
        "installments": _installments(payable),
        "shipping": _shipping(payable, city),
    }


def _installments(payable: Decimal) -> list[dict[str, object]]:
    plans: list[dict[str, object]] = []
    for count in range(1, 13):
        amount = (payable / Decimal(count)).quantize(_CENT, rounding=ROUND_HALF_UP)
        if _installment_allowed(count, amount):
            plans.append({"count": count, "amount": _money(amount)})
    return plans


def _installment_allowed(count: int, amount: Decimal) -> bool:
    if count == 1:
        return True
    if count <= 3:
        return amount >= Decimal("50.00")
    if count <= 6:
        return amount >= Decimal("80.00")
    return amount >= Decimal("100.00")


def _shipping(payable: Decimal, city: str | None) -> dict[str, object]:
    if city is None or not city.strip():
        return {
            "city_required": True,
            "message": "Informe a cidade para calcular o frete.",
        }
    cleaned = city.strip()
    if _is_campo_grande(cleaned):
        free = payable > _FREE_SHIPPING_ABOVE
        return {
            "city": cleaned,
            "free": free,
            "cost": _money(Decimal("0.00") if free else _CAMPO_GRANDE_FEE),
            "business_days": "1 a 3",
        }
    return {
        "city": cleaned,
        "priced_at_checkout": True,
        "message": "O valor do frete é calculado pelo CEP na finalização da compra.",
        "carriers": _load_carriers(data_dir()),
    }


def _is_campo_grande(city: str) -> bool:
    folded = " ".join(city.casefold().replace("–", "-").split())
    folded = re.sub(r"\s*[-/]\s*ms$", "", folded).strip()
    folded = re.sub(r"\s+ms$", "", folded).strip()
    return folded == "campo grande"


def _load_carriers(directory: Path) -> list[dict[str, str]]:
    text = _manual_text(directory).replace("\u200b", "").replace("\u00a0", " ")
    carriers: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in _CARRIER_PATTERN.finditer(text):
        name = " ".join(match.group(1).split())
        timeframe = " ".join(match.group(2).split())
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        carriers.append({"name": name, "timeframe": timeframe})
    if len(carriers) < 3:
        raise RuntimeError("Shipping carriers PAC, SEDEX, and Jadlog were not found in the manual.")
    return carriers


def _manual_text(directory: Path) -> str:
    manual_dir = directory / "manual"
    markdowns = sorted(manual_dir.rglob("*.md")) if manual_dir.is_dir() else []
    if not markdowns:
        markdowns = sorted(path for path in directory.glob("*.md") if path.is_file())
    if markdowns:
        return "\n".join(path.read_text(encoding="utf-8") for path in markdowns)
    pdf_path = directory / "políticas_da_loja.pdf"
    reader = PdfReader(str(pdf_path))
    return "\n".join(page.extract_text(extraction_mode="layout") or "" for page in reader.pages)


def _money(value: Decimal) -> str:
    return f"{value.quantize(_CENT, rounding=ROUND_HALF_UP):.2f}"


def _percent(value: Decimal) -> str:
    quantized = value.quantize(_CENT, rounding=ROUND_HALF_UP)
    if quantized == quantized.to_integral_value():
        return str(quantized.to_integral_value())
    return f"{quantized:.2f}"


def _contains_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
