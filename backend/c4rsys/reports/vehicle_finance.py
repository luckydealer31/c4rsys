"""Money of one vehicle in its card (customer's decision of 13.11.2026): the cost of the
acquisition, what the company spent on the vehicle, what the vehicle brought and the result by
currency. The lines are those of the report «Payback of own vehicles» (§ 21.4.2), so the numbers
are the same. Money of the company is shown in full or not at all: with access to a part of the
accounts it is hidden, as in the report."""

from decimal import Decimal
from typing import Any

from c4rsys.companies.models import Company
from c4rsys.platform.commands import Actor
from c4rsys.reports.overview import _accounts, _all_accounts
from c4rsys.reports.rental import CENT, MoneyLine, vehicle_money


def _money(value: Decimal) -> str:
    return str(value.quantize(CENT))


def _line(line: MoneyLine) -> dict[str, Any]:
    m = line.movement
    return {
        "id": str(m.id),
        "number": m.number,
        "at": line.at.isoformat() if line.at else None,
        "amount": _money(line.amount),
        "currency": line.currency,
        "article": m.article.name if m.article_id and m.article else "",
        "article_code": m.article.code if m.article_id and m.article else "",
        "note": m.note or m.reason,
        "order_id": str(line.order.id) if line.order is not None else None,
        "order_number": line.order.number if line.order is not None else None,
    }


def card_finance(actor: Actor, company: Company, card: Any) -> dict[str, Any]:
    acquisition = (
        {"cost": _money(card.acquisition_cost), "currency": card.acquisition_currency_id}
        if card.acquisition_cost is not None and card.acquisition_currency_id
        else None
    )
    accounts = _accounts(actor, company)
    if accounts is None or len(accounts) != _all_accounts(company).count():
        return {"available": False, "acquisition": acquisition}
    invested, received, spent, got = vehicle_money(company, {card.id})
    # The cost of the acquisition is an expense of the result (decision of 13.11.2026): a new
    # vehicle starts at minus its price; income raises the result, expenses lower it.
    bought = (
        {card.acquisition_currency_id: card.acquisition_cost} if acquisition is not None else {}
    )
    codes = sorted({*invested[card.id], *received[card.id], *bought})
    return {
        "available": True,
        "acquisition": acquisition,
        "expenses": [_line(x) for x in reversed(spent)],
        "incomes": [_line(x) for x in reversed(got)],
        "totals": [
            {
                "currency": code,
                "income": _money(received[card.id].get(code, Decimal(0))),
                "expense": _money(invested[card.id].get(code, Decimal(0))),
                "acquisition": _money(bought.get(code, Decimal(0))),
                "result": _money(
                    received[card.id].get(code, Decimal(0))
                    - invested[card.id].get(code, Decimal(0))
                    - bought.get(code, Decimal(0))
                ),
            }
            for code in codes
        ],
    }
