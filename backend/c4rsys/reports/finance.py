"""The starter dashboard «Finance» (ТЗ § 21.5.1, § 21.1.2–21.1.4, § 17.1).

* Money is counted by the accounts the employee may view; debts of clients and to suppliers only
  with access to every account (§ 21.1.1).
* A money operation belongs to the period by its posting time; a reversal goes to its own period
  and takes back its record's kind (§ 21.1.4).
* Transfers between own accounts are shown apart and are neither receipts nor expenses; deposits
  received, returned and offset are apart from receipts (§ 21.5.1). Money back to clients is shown
  apart as refunds.
* Amounts stay in the currency of the operation; the summary in the main currency uses the system
  rate known at the moment of each operation (§ 17.1); operations without a rate are listed.
* Receipts minus refunds and expenses is «the result of the period», not a book profit.
"""

from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db.models import Case, DecimalField, F, Sum, When

from c4rsys.companies.models import Company
from c4rsys.platform.commands import Actor
from c4rsys.reports.overview import Block, _accounts, _all_accounts
from c4rsys.reports.scope import Area, Filters

CENT = Decimal("0.01")
Money = dict[str, Decimal]


def _rows(money: Money) -> list[dict[str, Any]]:
    return [{"currency": c, "amount": str(v.quantize(CENT))} for c, v in sorted(money.items()) if v]


class _Main:
    """Sums in the main currency by the rate at the moment of each operation."""

    def __init__(self, main: str | None) -> None:
        self.main = main
        self.total = Decimal(0)
        self.missing: set[str] = set()

    def add(self, amount: Decimal, currency: str, at: datetime | None) -> None:
        from c4rsys.finance import rates

        if self.main is None:
            return
        if currency == self.main:
            self.total += amount
            return
        found = rates.suggest(currency, self.main, at)
        if found is None:
            self.missing.add(currency)
            return
        self.total += amount * found.rate

    def payload(self) -> dict[str, Any] | None:
        if self.main is None:
            return None
        return {
            "amount": str(self.total.quantize(CENT)),
            "currency": self.main,
            "missing": sorted(self.missing),
        }


def _balances_at(account_ids: list[UUID], moment: datetime) -> dict[UUID, Decimal]:
    from c4rsys.finance.models import MoneyMovement

    signed = Case(
        When(direction="in", then=F("amount")),
        default=-F("amount"),
        output_field=DecimalField(max_digits=20, decimal_places=4),
    )
    return {
        row["cash_account_id"]: row["total"] or Decimal(0)
        for row in MoneyMovement.objects.filter(
            cash_account_id__in=account_ids, state="posted", posted_at__lt=moment
        )
        .values("cash_account_id")
        .annotate(total=Sum(signed))
    }


def build(actor: Actor, company: Company, filters: Filters) -> list[Block]:
    from c4rsys.finance.models import DepositEntry, MoneyMovement

    accounts = _accounts(actor, company)
    if accounts is None:
        return []
    whole = len(accounts) == _all_accounts(company).count()
    names = [a.name for a in accounts]
    where = Area(whole, frozenset())
    ids = [a.id for a in accounts]
    opening = _balances_at(ids, filters.start_at)
    closing = _balances_at(ids, filters.end_at)
    balance_rows = [
        {
            "account": a.name,
            "currency": a.currency_id,
            "opening": str(opening.get(a.id, Decimal(0)).quantize(CENT)),
            "closing": str(closing.get(a.id, Decimal(0)).quantize(CENT)),
        }
        for a in accounts
    ]
    deposit_movements = set(
        DepositEntry.objects.filter(company=company, kind__in=("received", "refund"))
        .exclude(money_movement__isnull=True)
        .values_list("money_movement_id", flat=True)
    )
    movements = (
        MoneyMovement.objects.filter(
            company=company,
            cash_account_id__in=ids,
            state="posted",
            posted_at__gte=filters.start_at,
            posted_at__lt=filters.end_at,
        )
        .exclude(kind="opening")
        .select_related(
            "reverses", "article", "location", "context", "vehicle", "client", "counterparty"
        )
    )
    if filters.location_id:
        movements = movements.filter(location_id=filters.location_id)
    if filters.vehicle_id:
        movements = movements.filter(vehicle_id=filters.vehicle_id)
    receipts: Money = defaultdict(Decimal)
    expenses: Money = defaultdict(Decimal)
    refunds: Money = defaultdict(Decimal)
    transfers: Money = defaultdict(Decimal)
    main_receipts = _Main(company.main_currency_id)
    main_expenses = _Main(company.main_currency_id)
    main_refunds = _Main(company.main_currency_id)
    # Cuts (§ 21.5.1): by article, location, context, client, counterparty, vehicle.
    cuts: dict[str, dict[tuple[str, str], dict[str, Decimal]]] = {
        key: defaultdict(lambda: defaultdict(Decimal))
        for key in ("article", "location", "context", "client", "counterparty", "vehicle")
    }

    def classify(m: Any) -> tuple[str | None, Decimal]:
        """The kind of a record for the period and its sign; a reversal takes back its own."""
        base = m.reverses if m.kind == "reversal" and m.reverses is not None else m
        sign = Decimal(-1) if base is not m else Decimal(1)
        if base.kind == "transfer":
            return ("transfer" if base.direction == "out" else None), sign
        if base.id in deposit_movements or base.kind == "deposit":
            return None, sign
        if base.kind == "refund":
            return "refund", sign
        return ("receipt" if base.direction == "in" else "expense"), sign

    for m in movements:
        kind, sign = classify(m)
        if kind is None:
            continue
        amount = m.amount * sign
        if kind == "transfer":
            transfers[m.currency_id] += amount
            continue
        target, main = {
            "receipt": (receipts, main_receipts),
            "expense": (expenses, main_expenses),
            "refund": (refunds, main_refunds),
        }[kind]
        target[m.currency_id] += amount
        main.add(amount, m.currency_id, m.posted_at)
        column = "receipts" if kind == "receipt" else "expenses" if kind == "expense" else "refunds"
        labels: dict[str, str | None] = {
            "article": getattr(m.article, "name", None) or (str(m.article) if m.article else None),
            "location": m.location.name if m.location else None,
            "context": f"{m.context.location.name} · {m.context.type}" if m.context else None,
            "client": str(m.client) if m.client else None,
            "counterparty": str(m.counterparty) if m.counterparty else None,
            "vehicle": m.vehicle.name if m.vehicle else None,
        }
        for key, label in labels.items():
            if label is not None:
                cuts[key][(label, m.currency_id)][column] += amount

    result: Money = defaultdict(Decimal)
    for money, sign in ((receipts, Decimal(1)), (refunds, Decimal(-1)), (expenses, Decimal(-1))):
        for c, v in money.items():
            result[c] += v * sign
    main_result = None
    if company.main_currency_id:
        main_result = {
            "amount": str(
                (main_receipts.total - main_refunds.total - main_expenses.total).quantize(CENT)
            ),
            "currency": company.main_currency_id,
            "missing": sorted(main_receipts.missing | main_refunds.missing | main_expenses.missing),
        }
    blocks = [
        Block(
            "finance.account_balances", "finance", "table", None, balance_rows, where, "/finance"
        ),
        Block(
            "finance.receipts",
            "finance",
            "money",
            main_receipts.payload(),
            _rows(receipts),
            where,
            "/finance",
        ),
        Block(
            "finance.expenses",
            "finance",
            "money",
            main_expenses.payload(),
            _rows(expenses),
            where,
            "/finance",
        ),
        Block(
            "finance.refunds",
            "finance",
            "money",
            main_refunds.payload(),
            _rows(refunds),
            where,
            "/finance",
        ),
        Block("finance.transfers", "finance", "money", None, _rows(transfers), where, "/finance"),
        Block("finance.result", "finance", "money", main_result, _rows(result), where, "/finance"),
    ]
    blocks += _deposits(company, filters, where)
    for key, data in cuts.items():
        rows = [
            {
                "name": label,
                "currency": currency,
                "receipts": str(v.get("receipts", Decimal(0)).quantize(CENT)),
                "expenses": str(v.get("expenses", Decimal(0)).quantize(CENT)),
                "refunds": str(v.get("refunds", Decimal(0)).quantize(CENT)),
            }
            for (label, currency), v in sorted(data.items())
        ]
        blocks.append(Block(f"finance.by_{key}", "finance", "table", None, rows, where))
    if whole:
        blocks += _orders_and_debts(company, filters, where)
    for b in blocks:
        if not whole:
            b.accounts = names
        if b.unit == "money" and not b.rows and b.value is None:
            b.value = 0
    return blocks


def _deposits(company: Company, filters: Filters, where: Area) -> list[Block]:
    """Deposits apart from the revenue (§ 21.5.1, § 14.7)."""
    from c4rsys.finance.models import DepositEntry

    sums: dict[str, Money] = {k: defaultdict(Decimal) for k in ("received", "refund", "offset")}
    for e in DepositEntry.objects.filter(
        company=company,
        kind__in=list(sums),
        created_at__gte=filters.start_at,
        created_at__lt=filters.end_at,
    ):
        sums[e.kind][e.currency_id] += e.amount
    return [
        Block(
            "finance.deposits_received", "finance", "money", None, _rows(sums["received"]), where
        ),
        Block("finance.deposits_returned", "finance", "money", None, _rows(sums["refund"]), where),
        Block("finance.deposits_offset", "finance", "money", None, _rows(sums["offset"]), where),
    ]


def _orders_and_debts(company: Company, filters: Filters, where: Area) -> list[Block]:
    from c4rsys.finance import settlements
    from c4rsys.finance.models import ChargeEntry, PaymentAllocation

    charged: Money = defaultdict(Decimal)
    for c in ChargeEntry.objects.filter(
        company=company, created_at__gte=filters.start_at, created_at__lt=filters.end_at
    ):
        charged[c.currency_id] += c.amount
    paid: Money = defaultdict(Decimal)
    for a in PaymentAllocation.objects.filter(
        company=company,
        state="active",
        created_at__gte=filters.start_at,
        created_at__lt=filters.end_at,
    ):
        paid[a.currency_id] += a.amount
    receivable: Money = defaultdict(Decimal)
    for _client, figures in settlements.clients_with_balance(company.id):
        for currency, amount in figures.balance.items():
            if amount < 0:
                receivable[currency] -= amount
    payable: Money = defaultdict(Decimal)
    for _counterparty, owed in settlements.counterparties_with_balance(company.id):
        for currency, amount in owed.amounts.items():
            if amount > 0:
                payable[currency] += amount
    return [
        Block("finance.orders_charged", "finance", "money", None, _rows(charged), where, "/orders"),
        Block("finance.orders_paid", "finance", "money", None, _rows(paid), where, "/orders"),
        Block("finance.receivable", "finance", "money", None, _rows(receivable), where, "/clients"),
        Block("finance.payable", "finance", "money", None, _rows(payable), where, "/clients"),
    ]
