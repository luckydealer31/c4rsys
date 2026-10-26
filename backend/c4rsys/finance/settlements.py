"""Settlements with parties: who owes the company and whom the company owes (ТЗ § 17.2.3).

Clients (§ 6.7.2, § 17.2.10–17.2.14, § 17.2.17): the settlement balance per currency, split into
available funds and unpaid charges, initial obligations, refunds of available funds.

Suppliers (§ 11.2.4, § 11.4.2, § 17.2.14): what is owed for posted purchase receipts, payments
and money back linked to purchases and receipts, results of returns, initial obligations.

Movements of money and obligations stay separate (§ 17.2.6): an obligation is a settlement entry
or a charge, money is a money record.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db.models import Q, QuerySet, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from c4rsys.finance import articles, rates
from c4rsys.finance.models import (
    CashAccount,
    ChargeEntry,
    FinanceArticle,
    MoneyMovement,
    Offset,
    PaymentAllocation,
    SettlementEntry,
    SupplierAllocation,
)
from c4rsys.orders import services as orders
from c4rsys.orders.models import Order
from c4rsys.parties import counterparties
from c4rsys.parties import services as parties
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.platform.counters import next_number
from c4rsys.platform.models import Currency
from c4rsys.platform.money import round_money
from c4rsys.warehouse import settlements as warehouse

IN = MoneyMovement.Direction.IN
OUT = MoneyMovement.Direction.OUT
ZERO = Decimal(0)

Amounts = dict[str, Decimal]


def _add(target: Amounts, currency: str, amount: Decimal) -> None:
    target[currency] = target.get(currency, ZERO) + amount


def _entries_net(entries: QuerySet[SettlementEntry]) -> Amounts:
    """What the company owes the party by entries: + company is the debtor, − the party is."""
    result: Amounts = {}
    rows = entries.values("currency_id", "debtor").annotate(total=Sum("amount"))
    for row in rows:
        sign = 1 if row["debtor"] == SettlementEntry.Debtor.COMPANY else -1
        _add(result, row["currency_id"], sign * row["total"])
    return result


# --- Clients --------------------------------------------------------------------------------


def lock_client(company_id: UUID, client_id: UUID) -> Any:
    """Serializes commands that spend the client's available funds (refunds, set-offs)."""
    client = parties.clients_of(company_id).select_for_update().filter(pk=client_id).first()
    if client is None:
        raise errors.not_found()
    return client


def _client_money(company_id: UUID, client_id: UUID) -> Amounts:
    """Money received from and paid back to the client, in the currency of their funds."""
    query = MoneyMovement.objects.filter(
        company_id=company_id, client_id=client_id, state=MoneyMovement.State.POSTED
    ).filter(
        Q(kind__in=[MoneyMovement.Kind.PAYMENT, MoneyMovement.Kind.REFUND])
        | Q(
            kind=MoneyMovement.Kind.REVERSAL,
            reverses__kind__in=[MoneyMovement.Kind.PAYMENT, MoneyMovement.Kind.REFUND],
        )
    )
    rows = (
        query.annotate(
            funds_cur=Coalesce("funds_currency_id", "currency_id"),
            funds=Coalesce("funds_amount", "amount"),
        )
        .values("funds_cur", "direction")
        .annotate(total=Sum("funds"))
    )
    result: Amounts = {}
    for row in rows:
        _add(result, row["funds_cur"], row["total"] if row["direction"] == IN else -row["total"])
    return result


def _exchanged(rows: QuerySet[Any]) -> Amounts:
    """Allocations and offsets in another currency move money from the funds' currency to the
    order's: − amount in the funds' currency, + settled amount in the order's (§ 17.1.4)."""
    result: Amounts = {}
    for row in rows.values("currency_id").annotate(total=Sum("amount")):
        _add(result, row["currency_id"], -row["total"])
    for row in rows.values("settled_currency_id").annotate(total=Sum("settled_amount")):
        _add(result, row["settled_currency_id"], row["total"])
    return result


def _order_debts(company_id: UUID, client_id: UUID) -> Amounts:
    """Unpaid charges of the client's orders; an overpaid order owes nothing."""
    result: Amounts = {}
    query = orders.orders_of(company_id).filter(client_id=client_id)
    for order in query.exclude(state=Order.State.CANCELLED):
        for currency, money in orders.amounts_of(order).items():
            if money.charged > money.paid:
                _add(result, currency, money.charged - money.paid)
    return result


def opening_left(entry: SettlementEntry) -> Decimal:
    """What is still owed of an initial debt of a client after active offsets."""
    if hasattr(entry, "cancelled_by"):
        return ZERO
    settled = entry.offsets.filter(state=Offset.State.ACTIVE).aggregate(
        total=Sum(Coalesce("settled_amount", "amount"))
    )["total"]
    return entry.amount - (settled or ZERO)


@dataclass
class ClientFigures:
    """Per currency (§ 17.2.13): balance = available − unpaid; positive — advance or
    overpayment, negative — debt."""

    balance: Amounts = field(default_factory=dict)
    available: Amounts = field(default_factory=dict)
    unpaid: Amounts = field(default_factory=dict)


def client_figures(company_id: UUID, client_id: UUID) -> ClientFigures:
    balance = _client_money(company_id, client_id)
    for currency, amount in _entries_net(
        SettlementEntry.objects.filter(company_id=company_id, client_id=client_id)
    ).items():
        _add(balance, currency, amount)
    charged = (
        ChargeEntry.objects.filter(company_id=company_id, client_id=client_id)
        .values("currency_id")
        .annotate(total=Sum("amount"))
    )
    for row in charged:
        _add(balance, row["currency_id"], -row["total"])
    for exchanged in (
        PaymentAllocation.objects.filter(
            company_id=company_id,
            money_movement__client_id=client_id,
            state=PaymentAllocation.State.ACTIVE,
            settled_currency__isnull=False,
        ),
        Offset.objects.filter(
            company_id=company_id,
            client_id=client_id,
            state=Offset.State.ACTIVE,
            settled_currency__isnull=False,
        ),
    ):
        for currency, amount in _exchanged(exchanged).items():
            _add(balance, currency, amount)

    unpaid = _order_debts(company_id, client_id)
    openings = SettlementEntry.objects.filter(
        company_id=company_id,
        client_id=client_id,
        kind=SettlementEntry.Kind.OPENING,
        debtor=SettlementEntry.Debtor.PARTY,
    )
    for entry in openings:
        left = opening_left(entry)
        if left > 0:
            _add(unpaid, entry.currency_id, left)
    available = {c: balance.get(c, ZERO) + unpaid.get(c, ZERO) for c in {*balance, *unpaid}}
    return ClientFigures(balance=balance, available=available, unpaid=unpaid)


def clients_with_balance(company_id: UUID) -> list[tuple[UUID, ClientFigures]]:
    """Clients that owe the company or have money with it (§ 17.2.3)."""
    ids: set[UUID] = set()
    ids |= set(
        MoneyMovement.objects.filter(company_id=company_id, client__isnull=False).values_list(
            "client_id", flat=True
        )
    )
    ids |= set(
        ChargeEntry.objects.filter(company_id=company_id).values_list("client_id", flat=True)
    )
    ids |= set(
        SettlementEntry.objects.filter(company_id=company_id, client__isnull=False).values_list(
            "client_id", flat=True
        )
    )
    result = []
    for client_id in ids:
        figures = client_figures(company_id, client_id)
        if any(v != 0 for v in (*figures.balance.values(), *figures.unpaid.values())):
            result.append((client_id, figures))
    return result


# --- Suppliers ------------------------------------------------------------------------------


def _supplier_money(company_id: UUID, counterparty_id: UUID | None = None) -> QuerySet[Any]:
    query = MoneyMovement.objects.filter(
        company_id=company_id, state=MoneyMovement.State.POSTED, counterparty__isnull=False
    ).filter(
        Q(kind=MoneyMovement.Kind.SUPPLIER)
        | Q(kind=MoneyMovement.Kind.REVERSAL, reverses__kind=MoneyMovement.Kind.SUPPLIER)
    )
    if counterparty_id is not None:
        query = query.filter(counterparty_id=counterparty_id)
    return query


@dataclass
class Payable:
    """What the company owes a counterparty per currency: positive — the company owes,
    negative — prepaid or owed to the company. `incomplete` — some receipt has no known cost."""

    amounts: Amounts = field(default_factory=dict)
    incomplete: bool = False


def payable(company_id: UUID, counterparty_id: UUID) -> Payable:
    result = Payable()
    for value in warehouse.receipts(company_id, supplier_id=counterparty_id):
        _add(result.amounts, value.currency, value.amount)
        result.incomplete = result.incomplete or value.incomplete
    for currency, amount in _entries_net(
        SettlementEntry.objects.filter(company_id=company_id, counterparty_id=counterparty_id)
    ).items():
        _add(result.amounts, currency, amount)
    rows = (
        _supplier_money(company_id, counterparty_id)
        .values("currency_id", "direction")
        .annotate(total=Sum("amount"))
    )
    for row in rows:
        _add(
            result.amounts,
            row["currency_id"],
            -row["total"] if row["direction"] == OUT else row["total"],
        )
    return result


def counterparties_with_balance(company_id: UUID) -> list[tuple[UUID, Payable]]:
    ids: set[UUID] = {v.supplier_id for v in warehouse.receipts(company_id) if v.supplier_id}
    ids |= set(_supplier_money(company_id).values_list("counterparty_id", flat=True))
    ids |= set(
        SettlementEntry.objects.filter(
            company_id=company_id, counterparty__isnull=False
        ).values_list("counterparty_id", flat=True)
    )
    result = []
    for counterparty_id in ids:
        owed = payable(company_id, counterparty_id)
        if any(v != 0 for v in owed.amounts.values()) or owed.incomplete:
            result.append((counterparty_id, owed))
    return result


def _paid_to(
    company_id: UUID, *, purchase_id: UUID | None = None, receipt_ids: list[UUID] | None = None
) -> Decimal:
    query = SupplierAllocation.objects.filter(
        company_id=company_id,
        state=SupplierAllocation.State.ACTIVE,
        money_movement__state=MoneyMovement.State.POSTED,
        money_movement__reversed_by__isnull=True,
    )
    if purchase_id is not None:
        query = query.filter(purchase_id=purchase_id)
    if receipt_ids is not None:
        query = query.filter(receipt_id__in=receipt_ids)
    total = ZERO
    for row in query.values("money_movement__direction").annotate(total=Sum("amount")):
        sign = 1 if row["money_movement__direction"] == OUT else -1
        total += sign * row["total"]
    return total


def _returned(company_id: UUID, receipt_ids: list[UUID]) -> Decimal:
    entries = SettlementEntry.objects.filter(
        company_id=company_id,
        kind=SettlementEntry.Kind.RETURN_CREDIT,
        stock_operation__receipt_id__in=receipt_ids,
        cancelled_by__isnull=True,
    ).aggregate(total=Sum("amount"))["total"]
    return entries or ZERO


def statement(
    company_id: UUID, *, purchase_id: UUID | None = None, receipt_id: UUID | None = None
) -> dict[str, Any]:
    """The state of settlements of a purchase or a receipt (§ 11.2.4): value of the received
    goods, settled returns, paid, and what is left to pay (negative — prepaid)."""
    if purchase_id is not None:
        values = warehouse.receipts(company_id, purchase_id=purchase_id)
        paid = _paid_to(company_id, purchase_id=purchase_id) + _paid_to(
            company_id, receipt_ids=[v.id for v in values]
        )
    elif receipt_id is not None:
        values = warehouse.receipts(company_id, receipt_ids=[receipt_id])
        paid = _paid_to(company_id, receipt_ids=[receipt_id])
    else:
        raise ValueError("A purchase or a receipt is needed")
    target = (
        warehouse.purchase(company_id, purchase_id)
        if purchase_id is not None
        else warehouse.receipt(company_id, receipt_id)  # type: ignore[arg-type]
    )
    if target is None:
        raise errors.not_found()
    received = sum((v.amount for v in values), ZERO)
    returned = _returned(company_id, [v.id for v in values])
    return {
        "currency": target.currency,
        "received": received,
        "incomplete": any(v.incomplete for v in values),
        "returned": returned,
        "paid": paid,
        "to_pay": received - returned - paid,
    }


# --- Initial obligations (§ 17.2.14) --------------------------------------------------------


@dataclass(frozen=True)
class NewOpening:
    debtor: str
    amount: Decimal
    currency: str
    as_of: date
    client_id: UUID | None = None
    counterparty_id: UUID | None = None
    note: str = ""


def entry_snapshot(entry: SettlementEntry) -> dict[str, Any]:
    return {
        "kind": entry.kind,
        "client": str(entry.client_id) if entry.client_id else None,
        "counterparty": str(entry.counterparty_id) if entry.counterparty_id else None,
        "debtor": entry.debtor,
        "amount": entry.amount,
        "currency": entry.currency_id,
        "as_of": entry.as_of.isoformat(),
        "return_result": entry.return_result,
        "cancels": str(entry.cancels_id) if entry.cancels_id else None,
        "note": entry.note,
    }


def create_opening(ctx: CommandContext, company_id: UUID, data: NewOpening) -> SettlementEntry:
    """A client's debt or advance (old deposits go to the ordinary balance), or a debt between
    the company and a counterparty, at the start of accounting. No money moves; no old order or
    rental is needed. Later it is settled by ordinary operations."""
    if data.client_id is not None:
        parties.get_client(company_id, data.client_id)
    if data.counterparty_id is not None:
        counterparties.get(company_id, data.counterparty_id)
    entry = SettlementEntry.objects.create(
        company_id=company_id,
        kind=SettlementEntry.Kind.OPENING,
        client_id=data.client_id,
        counterparty_id=data.counterparty_id,
        debtor=data.debtor,
        amount=data.amount,
        currency_id=data.currency,
        as_of=data.as_of,
        note=data.note,
        created_by_user_id=ctx.actor.user_id,
        created_at=timezone.now(),
    )
    ctx.audit(
        "settlement.opening_recorded",
        object_type="settlement_entry",
        object_id=entry.id,
        after=entry_snapshot(entry),
    )
    return entry


def cancel_entry(
    ctx: CommandContext, company_id: UUID, entry_id: UUID, note: str
) -> SettlementEntry:
    """A mistaken entry is cancelled by an opposite one; both stay in history."""
    entry = (
        SettlementEntry.objects.select_for_update()
        .filter(company_id=company_id, pk=entry_id)
        .exclude(kind=SettlementEntry.Kind.CANCELLATION)
        .first()
    )
    if entry is None:
        raise errors.not_found()
    if SettlementEntry.objects.filter(cancels=entry).exists():
        raise errors.business_rule("ENTRY_CANCELLED")
    if entry.offsets.filter(state=Offset.State.ACTIVE).exists():
        # The set-offs are cancelled first (§ 17.2.15).
        raise errors.business_rule("ENTRY_HAS_OFFSETS")
    opposite = (
        SettlementEntry.Debtor.PARTY
        if entry.debtor == SettlementEntry.Debtor.COMPANY
        else SettlementEntry.Debtor.COMPANY
    )
    cancellation = SettlementEntry.objects.create(
        company_id=company_id,
        kind=SettlementEntry.Kind.CANCELLATION,
        client_id=entry.client_id,
        counterparty_id=entry.counterparty_id,
        debtor=opposite,
        amount=entry.amount,
        currency_id=entry.currency_id,
        as_of=timezone.localdate(),
        cancels=entry,
        note=note,
        created_by_user_id=ctx.actor.user_id,
        created_at=timezone.now(),
    )
    ctx.audit(
        "settlement.entry_cancelled",
        object_type="settlement_entry",
        object_id=entry.id,
        before=entry_snapshot(entry),
        after={"cancelled_by": str(cancellation.id), "note": note},
    )
    return cancellation


# --- Returns to suppliers (§ 11.4.2) --------------------------------------------------------


def settle_return(
    ctx: CommandContext,
    company_id: UUID,
    operation_id: UUID,
    result: str,
    amount: Decimal | None,
    note: str,
) -> SettlementEntry:
    """The financial result of a return: the debt to the supplier goes down by the value of the
    returned goods. Money back is then received as a supplier receipt; a replacement comes as a
    new receipt. The physical return alone does not change settlements."""
    operation = warehouse.return_operation(company_id, operation_id)
    if operation is None:
        raise errors.not_found()
    receipt = operation.receipt
    if receipt is None or receipt.supplier_id is None:
        raise errors.business_rule("RETURN_WITHOUT_SUPPLIER")
    if SettlementEntry.objects.filter(stock_operation_id=operation.id).exists():
        raise errors.business_rule("RETURN_SETTLED")
    value = warehouse.returns(company_id, operation_ids=[operation.id])[0]
    if amount is None:
        if value.incomplete:
            raise errors.validation_error("amount", code="RETURN_COST_UNKNOWN")
        amount = value.amount
    if amount <= 0:
        raise errors.validation_error("amount", code="AMOUNT_INVALID")
    entry = SettlementEntry.objects.create(
        company_id=company_id,
        kind=SettlementEntry.Kind.RETURN_CREDIT,
        counterparty_id=receipt.supplier_id,
        debtor=SettlementEntry.Debtor.PARTY,
        amount=amount,
        currency_id=receipt.currency,
        as_of=timezone.localdate(),
        stock_operation_id=operation.id,
        return_result=result,
        note=note,
        created_by_user_id=ctx.actor.user_id,
        created_at=timezone.now(),
    )
    ctx.audit(
        "settlement.return_settled",
        object_type="settlement_entry",
        object_id=entry.id,
        after={
            **entry_snapshot(entry),
            "return": str(operation.id),
            "return_number": operation.number,
        },
    )
    return entry


# --- Supplier payments (§ 11.2.4) -----------------------------------------------------------


@dataclass(frozen=True)
class SupplierLink:
    amount: Decimal
    purchase_id: UUID | None = None
    receipt_id: UUID | None = None


@dataclass(frozen=True)
class SupplierPayment:
    cash_account_id: UUID
    counterparty_id: UUID
    direction: str  # out — paid to the supplier, in — money back from the supplier
    amount: Decimal
    currency: str
    article_id: UUID
    links: tuple[SupplierLink, ...] = ()
    note: str = ""


def _locked_account(company_id: UUID, account_id: UUID) -> CashAccount:
    account = (
        CashAccount.objects.select_for_update().filter(company_id=company_id, pk=account_id).first()
    )
    if account is None:
        raise errors.not_found()
    if account.state != CashAccount.State.ACTIVE:
        raise errors.business_rule("CASH_ACCOUNT_ARCHIVED", field="cash_account_id")
    return account


def pay_supplier(ctx: CommandContext, company_id: UUID, data: SupplierPayment) -> MoneyMovement:
    """A payment to a supplier, possibly before the goods come, in parts, or for received goods;
    the payment itself does not change the stock (§ 11.2.4). Parts may be linked to purchases and
    receipts of this supplier; the rest stays a payment on account.

    TODO(spec): a link in another currency than the purchase (§ 17.1.4) is not made yet."""
    account = _locked_account(company_id, data.cash_account_id)
    if account.currency_id != data.currency:
        raise errors.business_rule(
            "CURRENCY_MISMATCH", field="currency", account_currency=account.currency_id
        )
    counterparty = counterparties.get(company_id, data.counterparty_id)
    article = articles.usable(company_id, data.article_id, FinanceArticle.Kind.EXPENSE)
    if sum((link.amount for link in data.links), ZERO) > data.amount:
        raise errors.business_rule("ALLOCATION_EXCEEDS_PAYMENT", field="links")
    for index, link in enumerate(data.links):
        target = (
            warehouse.purchase(company_id, link.purchase_id)
            if link.purchase_id
            else warehouse.receipt(company_id, link.receipt_id)
            if link.receipt_id
            else None
        )
        if target is None:
            raise errors.validation_error(f"links[{index}]", code="TARGET_INVALID")
        if target.supplier_id != counterparty.id:
            raise errors.business_rule("SUPPLIER_MISMATCH", field=f"links[{index}]")
        if target.currency != data.currency:
            raise errors.business_rule("CURRENCY_MISMATCH", field=f"links[{index}]")

    now = timezone.now()
    movement = MoneyMovement.objects.create(
        company_id=company_id,
        number=next_number(company_id, "payment"),
        cash_account=account,
        kind=MoneyMovement.Kind.SUPPLIER,
        direction=data.direction,
        amount=data.amount,
        currency_id=data.currency,
        counterparty=counterparty,
        article=article,
        state=MoneyMovement.State.POSTED,
        method="",
        note=data.note,
        posted_at=now,
        created_by_user_id=ctx.actor.user_id,
        created_at=now,
    )
    for link in data.links:
        SupplierAllocation.objects.create(
            company_id=company_id,
            money_movement=movement,
            purchase_id=link.purchase_id,
            receipt_id=link.receipt_id,
            amount=link.amount,
            created_by_user_id=ctx.actor.user_id,
            created_at=now,
        )
    ctx.audit(
        "supplier.paid" if data.direction == OUT else "supplier.money_back",
        object_type="money_movement",
        object_id=movement.id,
        after={
            "number": movement.number,
            "cash_account": str(account.id),
            "counterparty": str(counterparty.id),
            "amount": data.amount,
            "currency": data.currency,
            "article": str(article.id),
            "links": [
                {
                    "purchase": str(link.purchase_id) if link.purchase_id else None,
                    "receipt": str(link.receipt_id) if link.receipt_id else None,
                    "amount": link.amount,
                }
                for link in data.links
            ],
            "note": data.note,
        },
    )
    return movement


# --- Refund of available funds (§ 17.2.10, § 17.2.17) ---------------------------------------


@dataclass(frozen=True)
class Refund:
    cash_account_id: UUID
    client_id: UUID
    funds_amount: Decimal  # taken from the client's available funds
    funds_currency: str
    rate: Decimal | None = None  # to the account's currency when it differs
    note: str = ""


def refund(
    ctx: CommandContext, company_id: UUID, data: Refund, *, may_override_rate: bool
) -> MoneyMovement:
    """Only the available rest can be paid back; set-off or held money is released first. In
    another currency the account pays the converted amount and the client's funds go down by the
    returned amount, with the rate of this payout; earlier receipts do not change."""
    account = _locked_account(company_id, data.cash_account_id)
    client = lock_client(company_id, data.client_id)
    available = client_figures(company_id, client.id).available.get(data.funds_currency, ZERO)
    if data.funds_amount > available:
        places = Currency.objects.get(code=data.funds_currency).minor_units
        raise errors.business_rule(
            "REFUND_EXCEEDS_AVAILABLE",
            field="funds_amount",
            available=str(round_money(max(available, ZERO), places)),
            currency=data.funds_currency,
        )
    applied = None
    amount = data.funds_amount
    if account.currency_id != data.funds_currency:
        applied = rates.apply(
            data.funds_currency, account.currency_id, data.rate, may_override=may_override_rate
        )
        amount = rates.convert(data.funds_amount, applied.rate, account.currency_id)
        if amount <= 0:
            raise errors.validation_error("funds_amount", code="AMOUNT_TOO_SMALL")
    now = timezone.now()
    movement = MoneyMovement.objects.create(
        company_id=company_id,
        number=next_number(company_id, "payment"),
        cash_account=account,
        kind=MoneyMovement.Kind.REFUND,
        direction=OUT,
        amount=amount,
        currency_id=account.currency_id,
        client=client,
        funds_amount=data.funds_amount if applied else None,
        funds_currency_id=data.funds_currency if applied else None,
        rate_fetch=applied.fetch if applied else None,
        suggested_rate=applied.suggested if applied else None,
        applied_rate=applied.rate if applied else None,
        rate_manual=applied.manual if applied else False,
        state=MoneyMovement.State.POSTED,
        method="",
        note=data.note,
        posted_at=now,
        created_by_user_id=ctx.actor.user_id,
        created_at=now,
    )
    ctx.audit(
        "client.refunded",
        object_type="money_movement",
        object_id=movement.id,
        after={
            "number": movement.number,
            "cash_account": str(account.id),
            "client": str(client.id),
            "amount": amount,
            "currency": account.currency_id,
            "funds_amount": data.funds_amount,
            "funds_currency": data.funds_currency,
            "available_after": available - data.funds_amount,
            **(rates.snapshot(applied) if applied else {}),
        },
    )
    return movement


def lists(company_id: UUID) -> dict[str, Any]:
    """Who owes the company and whom the company owes (§ 17.2.3)."""
    clients = clients_with_balance(company_id)
    suppliers = counterparties_with_balance(company_id)
    client_names = {
        c.id: parties.display_name(c)
        for c in parties.clients_of(company_id).filter(pk__in=[i for i, _ in clients])
    }
    supplier_names = counterparties.names(company_id, {i for i, _ in suppliers})
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for client_id, figures in clients:
        grouped["clients"].append(
            {"id": client_id, "name": client_names.get(client_id, ""), "figures": figures}
        )
    for counterparty_id, owed in suppliers:
        grouped["counterparties"].append(
            {
                "id": counterparty_id,
                "name": supplier_names.get(counterparty_id, ""),
                "payable": owed,
            }
        )
    return grouped
