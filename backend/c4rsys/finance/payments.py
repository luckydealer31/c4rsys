"""Receipt of money and its allocation to orders (ТЗ § 12.2.4–12.2.5, § 13.6.3, § 13.6.8,
§ 14.7.3, § 17.2.6, § 17.2.10–17.2.13).

The receipt is an independent money record; allocations assign parts of it to orders. An
unallocated part stays available funds of the client, and an allocation may exceed what the order
still owes — the order then shows an overpayment (decisions 22.10.2026). Nothing is paid back
automatically (§ 13.6.3).
"""

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from django.db.models import Sum
from django.utils import timezone

from c4rsys.finance.models import CashAccount, ChargeEntry, MoneyMovement, PaymentAllocation
from c4rsys.orders import services as orders
from c4rsys.parties import services as parties
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.platform.counters import next_number


@dataclass(frozen=True)
class Allocation:
    order_id: UUID
    amount: Decimal


@dataclass(frozen=True)
class CashReceipt:
    cash_account_id: UUID
    client_id: UUID
    amount: Decimal
    currency: str
    allocations: tuple[Allocation, ...]
    note: str = ""


def receive(ctx: CommandContext, request: CashReceipt) -> MoneyMovement:
    """Lock order: cash desk → orders (STAGE1_COMMANDS_API). The account type is not checked:
    money is received to an account available to the employee (decision 22.10.2026)."""
    company_id = ctx.actor.company_id
    if company_id is None:
        raise RuntimeError("Payments run in a company")
    account = (
        CashAccount.objects.select_for_update()
        .filter(company_id=company_id, pk=request.cash_account_id)
        .first()
    )
    if account is None:
        raise errors.not_found()
    if account.state != CashAccount.State.ACTIVE:
        raise errors.business_rule("CASH_ACCOUNT_ARCHIVED", field="cash_account_id")
    if account.currency_id != request.currency:
        raise errors.business_rule(
            "CURRENCY_MISMATCH", field="currency", account_currency=account.currency_id
        )
    client = parties.get_client(company_id, request.client_id)
    total = sum((a.amount for a in request.allocations), Decimal(0))
    if total > request.amount:
        raise errors.business_rule("ALLOCATION_EXCEEDS_PAYMENT", field="allocations")

    locked = []
    for allocation in sorted(request.allocations, key=lambda a: a.order_id):
        order = orders.lock_open_order(company_id, allocation.order_id)
        if order.client_id != client.id:
            raise errors.business_rule("ORDER_CLIENT_MISMATCH", order_id=str(order.id))
        other = set(orders.amounts_of(order)) - {request.currency}
        if other:
            # Stage 1: one currency per order; exchange comes with § 17.1.3–17.1.4.
            raise errors.business_rule(
                "CURRENCY_MISMATCH", order_id=str(order.id), order_currencies=sorted(other)
            )
        locked.append((order, allocation))

    now = timezone.now()
    movement = MoneyMovement.objects.create(
        company_id=company_id,
        number=next_number(company_id, "payment"),
        cash_account=account,
        direction=MoneyMovement.Direction.IN,
        amount=request.amount,
        currency_id=request.currency,
        client=client,
        state=MoneyMovement.State.POSTED,
        method="cash",
        note=request.note,
        posted_at=now,
        created_by_user_id=ctx.actor.user_id,
        created_at=now,
    )
    for order, allocation in locked:
        PaymentAllocation.objects.create(
            company_id=company_id,
            money_movement=movement,
            order=order,
            purpose=PaymentAllocation.Purpose.ORDER_PAYMENT,
            amount=allocation.amount,
            currency_id=request.currency,
            created_by_user_id=ctx.actor.user_id,
            created_at=now,
        )
        orders.record_control_versions(ctx, order, "payment")
    ctx.audit(
        "payment.received",
        object_type="money_movement",
        object_id=movement.id,
        after={
            "number": movement.number,
            "cash_account": str(account.id),
            "client": str(client.id),
            "amount": request.amount,
            "currency": request.currency,
            "method": movement.method,
            "allocations": [
                {"order": str(o.id), "order_number": o.number, "amount": a.amount}
                for o, a in locked
            ],
            "unallocated": request.amount - total,
        },
    )
    return movement


# --- Amounts for orders and clients -----------------------------------------------------------


def paid_for_order(company_id: UUID, order_id: UUID) -> dict[str, Decimal]:
    """Provider for orders: active allocations per currency."""
    rows = (
        PaymentAllocation.objects.filter(
            company_id=company_id, order_id=order_id, state=PaymentAllocation.State.ACTIVE
        )
        .values("currency_id")
        .annotate(total=Sum("amount"))
    )
    return {r["currency_id"]: r["total"] for r in rows}


def client_balance(company_id: UUID, client_id: UUID) -> dict[str, Decimal]:
    """Settlement balance of the client per currency: money received minus charges (§ 6.7.2,
    § 17.2.13). Positive — advance or overpayment, negative — debt."""
    received = (
        MoneyMovement.objects.filter(
            company_id=company_id,
            client_id=client_id,
            direction=MoneyMovement.Direction.IN,
            state=MoneyMovement.State.POSTED,
        )
        .values("currency_id")
        .annotate(total=Sum("amount"))
    )
    charged = (
        ChargeEntry.objects.filter(company_id=company_id, client_id=client_id)
        .values("currency_id")
        .annotate(total=Sum("amount"))
    )
    balance: dict[str, Decimal] = {}
    for row in received:
        balance[row["currency_id"]] = balance.get(row["currency_id"], Decimal(0)) + row["total"]
    for row in charged:
        balance[row["currency_id"]] = balance.get(row["currency_id"], Decimal(0)) - row["total"]
    return balance


def client_debt(company_id: UUID, client_id: UUID) -> dict[str, Decimal]:
    return {c: -v for c, v in client_balance(company_id, client_id).items() if v < 0}


def history_for_order(company_id: UUID, order_id: UUID) -> list[tuple[str, str]]:
    """Payments allocated to the order, as journaled objects."""
    ids = (
        PaymentAllocation.objects.filter(company_id=company_id, order_id=order_id)
        .values_list("money_movement_id", flat=True)
        .distinct()
    )
    return [("money_movement", str(i)) for i in ids]
