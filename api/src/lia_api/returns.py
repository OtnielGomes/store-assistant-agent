"""Return-window check. Dates are computed in code; the model never writes SQL."""

import json
from datetime import date, timedelta
from typing import Annotated

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolArg, tool

from lia_api.orders import _conversation_id, _today, execute_order_lookup

_WINDOWS = (
    ("withdrawal_and_preference_exchange", 7),
    ("defect_exchange", 30),
    ("legal_warranty", 90),
)


def build_return_window_tool(database_url: str):
    """Tool the assistant uses to report exchange and warranty deadlines."""

    @tool
    def check_return_window(
        order_number: int,
        phone_last_four: str,
        config: Annotated[RunnableConfig, InjectedToolArg],
    ) -> str:
        """Informa os prazos de arrependimento, troca e garantia de um pedido conferido.

        Exige order_number e phone_last_four (exatamente quatro dígitos).
        A data de recebimento é a previsão de entrega. Devolve o prazo de 7 dias
        (arrependimento e troca por preferência), o de 30 dias (troca por defeito)
        e o de 90 dias (garantia legal), e se cada um ainda está aberto na data
        do relógio. Não aprova nem recusa troca, devolução, reembolso ou garantia.
        Telefone e e-mail nunca vêm na resposta.
        """
        result = execute_return_window(
            database_url,
            order_number=order_number,
            phone_last_four=phone_last_four,
            conversation_id=_conversation_id(config),
            today=_today(config),
        )
        return json.dumps(result, ensure_ascii=False)

    return check_return_window


def execute_return_window(
    database_url: str,
    *,
    order_number: int,
    phone_last_four: str,
    conversation_id: str,
    today: date,
) -> dict[str, object]:
    lookup = execute_order_lookup(
        database_url,
        order_number=order_number,
        phone_last_four=phone_last_four,
        conversation_id=conversation_id,
        today=today,
    )
    if not lookup["verified"]:
        return lookup
    order = lookup["order"]
    if not isinstance(order, dict):
        return lookup
    receipt = order["estimated_delivery"]
    if order["status"] != "delivered" or not isinstance(receipt, str) or not receipt:
        return {
            "verified": True,
            "verification_locked": False,
            "status": order["status"],
            "receipt_date": None,
            "deadlines": None,
        }
    receipt_date = date.fromisoformat(receipt)
    deadlines: dict[str, object] = {}
    for name, days in _WINDOWS:
        deadline = receipt_date + timedelta(days=days)
        deadlines[name] = {
            "days": days,
            "deadline": deadline.isoformat(),
            "deadline_day_month": deadline.strftime("%d/%m"),
            "open": today <= deadline,
        }
    return {
        "verified": True,
        "verification_locked": False,
        "status": order["status"],
        "receipt_date": receipt,
        "deadlines": deadlines,
    }
