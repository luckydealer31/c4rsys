"""Corrections of posted money records (ТЗ § 17.2.11, § 23.2.7) and split of an expense
(§ 17.3.3).

A posted record is never changed. A mistaken record is cancelled out by a reversal — the opposite
record that names it and carries a reason; both stay in history. The link of a payment to an
order is changed by cancelling the allocation and creating a new one; the amount is not corrected
this way. An expense may be split between vehicles and orders: the shares are analytic and add up
to the amount, the payout stays one.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.utils import timezone

from c4rsys.finance import charges, rates, settlements
from c4rsys.finance.models import (
    CashAccount,
    DepositEntry,
    ExpenseShare,
    MoneyMovement,
    PaymentAllocation,
    PaymentClientChange,
    SupplierAllocation,
    Transfer,
)
from c4rsys.fleet.models import VehicleCard
from c4rsys.orders import services as orders
from c4rsys.orders.models import Order
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.platform.counters import next_number

ZERO = Decimal(0)
REVERSIBLE = {
    MoneyMovement.Kind.MANUAL,
    MoneyMovement.Kind.PAYMENT,
    MoneyMovement.Kind.SUPPLIER,
    MoneyMovement.Kind.REFUND,
    MoneyMovement.Kind.TRANSFER,
    MoneyMovement.Kind.OPENING,
}


def _locked(company_id: UUID, movement_id: UUID) -> MoneyMovement:
    movement = (
        MoneyMovement.objects.select_for_update()
        .filter(company_id=company_id, pk=movement_id)
        .first()
    )
    if movement is None:
        raise errors.not_found()
    return movement


def accounts_of(movement: MoneyMovement) -> list[UUID]:
    """The accounts a correction touches: both sides of a transfer."""
    if movement.kind != MoneyMovement.Kind.TRANSFER:
        return [movement.cash_account_id]
    transfer = Transfer.objects.select_related("out_movement", "in_movement").get(
        **({"out_movement": movement} if movement.direction == "out" else {"in_movement": movement})
    )
    return [transfer.out_movement.cash_account_id, transfer.in_movement.cash_account_id]


def _opposite(ctx: CommandContext, movement: MoneyMovement, reason: str) -> MoneyMovement:
    now = timezone.now()
    return MoneyMovement.objects.create(
        company_id=movement.company_id,
        number=next_number(movement.company_id, "payment"),
        cash_account_id=movement.cash_account_id,
        kind=MoneyMovement.Kind.REVERSAL,
        direction=MoneyMovement.Direction.IN
        if movement.direction == MoneyMovement.Direction.OUT
        else MoneyMovement.Direction.OUT,
        amount=movement.amount,
        currency_id=movement.currency_id,
        client_id=settlements.client_of(movement),
        counterparty_id=movement.counterparty_id,
        article_id=movement.article_id,
        reverses=movement,
        reason=reason,
        funds_amount=movement.funds_amount,
        funds_currency_id=movement.funds_currency_id,
        applied_rate=movement.applied_rate,
        location_id=movement.location_id,
        context_id=movement.context_id,
        vehicle_id=movement.vehicle_id,
        state=MoneyMovement.State.POSTED,
        method="",
        posted_at=now,
        created_by_user_id=ctx.actor.user_id,
        created_at=now,
    )


def reverse(
    ctx: CommandContext, company_id: UUID, movement_id: UUID, reason: str
) -> list[MoneyMovement]:
    """Cancels a posted record by the opposite one. A payment allocated to orders is first
    unlinked (its allocations are changed separately); a transfer is reversed on both accounts.
    A reversed initial balance of an account may then be entered again (records.set_opening)."""
    movement = _locked(company_id, movement_id)
    if movement.kind not in REVERSIBLE or movement.state != MoneyMovement.State.POSTED:
        raise errors.business_rule("RECORD_NOT_REVERSIBLE")
    if MoneyMovement.objects.filter(reverses=movement).exists():
        raise errors.business_rule("RECORD_REVERSED")
    for account in (
        CashAccount.objects.select_for_update().filter(pk__in=accounts_of(movement)).order_by("id")
    ):
        if account.state != CashAccount.State.ACTIVE:
            raise errors.business_rule("CASH_ACCOUNT_ARCHIVED")
    if movement.allocations.filter(state=PaymentAllocation.State.ACTIVE).exists():
        raise errors.business_rule("RECORD_ALLOCATED")
    if DepositEntry.objects.filter(money_movement=movement).exists():
        # TODO(spec): a receipt with a deposit part is corrected with the deposit (§ 14.7.3).
        raise errors.business_rule("RECORD_HAS_DEPOSIT")
    created = []
    targets = [movement]
    if movement.kind == MoneyMovement.Kind.TRANSFER:
        transfer = Transfer.objects.select_related("out_movement", "in_movement").get(
            **(
                {"out_movement": movement}
                if movement.direction == "out"
                else {"in_movement": movement}
            )
        )
        other = (
            transfer.in_movement
            if transfer.out_movement_id == movement.id
            else transfer.out_movement
        )
        if MoneyMovement.objects.filter(reverses=other).exists():
            raise errors.business_rule("RECORD_REVERSED")
        targets.append(other)
    for target in targets:
        created.append(_opposite(ctx, target, reason))
    # A reversed supplier payment no longer pays its purchases and receipts.
    for allocation in SupplierAllocation.objects.filter(
        money_movement__in=targets, state=SupplierAllocation.State.ACTIVE
    ):
        allocation.state = SupplierAllocation.State.CANCELLED
        allocation.save(update_fields=["state"])
    for share in ExpenseShare.objects.filter(
        money_movement__in=targets, state=ExpenseShare.State.ACTIVE
    ):
        share.state = ExpenseShare.State.CANCELLED
        share.save(update_fields=["state"])
    ctx.audit(
        "money.reversed",
        object_type="money_movement",
        object_id=movement.id,
        after={
            "number": movement.number,
            "kind": movement.kind,
            "amount": movement.amount,
            "currency": movement.currency_id,
            "reason": reason,
            "reversals": [{"id": str(m.id), "number": m.number} for m in created],
        },
    )
    return created


def relink_allocation(
    ctx: CommandContext,
    company_id: UUID,
    allocation_id: UUID,
    order_id: UUID | None,
    reason: str,
    *,
    rate: Decimal | None = None,
    may_override_rate: bool = False,
) -> PaymentAllocation | None:
    """Removes the current link of a payment to an order or moves it to another order of the same
    client; the old link stays in history. The amount is not corrected this way (§ 17.2.11,
    § 12.3.7). Without a new order the money becomes the client's available funds again."""
    allocation = (
        PaymentAllocation.objects.select_for_update()
        .select_related("money_movement", "order")
        .filter(company_id=company_id, pk=allocation_id)
        .first()
    )
    if allocation is None:
        raise errors.not_found()
    if allocation.state != PaymentAllocation.State.ACTIVE:
        raise errors.business_rule("ALLOCATION_CANCELLED")
    movement = allocation.money_movement
    new_order = None
    if order_id is not None:
        if order_id == allocation.order_id:
            raise errors.validation_error("order_id", code="ORDER_SAME")
        new_order = (
            Order.objects.select_for_update().filter(company_id=company_id, pk=order_id).first()
        )
        if new_order is None:
            raise errors.not_found()
        if new_order.client_id != settlements.client_of(movement):
            raise errors.business_rule("ORDER_CLIENT_MISMATCH", field="order_id")
        if new_order.state == Order.State.CANCELLED:
            raise errors.business_rule("ORDER_CANCELLED", field="order_id")
        charged = set(charges.charged_for_order(company_id, new_order.id))
        if len(charged) > 1:
            raise errors.business_rule("ORDER_CURRENCIES_MIXED", field="order_id")
        target_currency = charged.pop() if charged else allocation.currency_id
        # The payment part stays in its currency; another order's currency takes a new rate,
        # the rate of the first link is not reused (§ 17.1.3–17.1.4).
        applied = None
        if target_currency != allocation.currency_id:
            applied = rates.apply(
                allocation.currency_id, target_currency, rate, may_override=may_override_rate
            )
    allocation.state = PaymentAllocation.State.CANCELLED
    allocation.save(update_fields=["state"])
    replacement = None
    if new_order is not None:
        replacement = PaymentAllocation.objects.create(
            company_id=company_id,
            money_movement=movement,
            order=new_order,
            purpose=allocation.purpose,
            amount=allocation.amount,
            currency_id=allocation.currency_id,
            settled_amount=rates.convert(allocation.amount, applied.rate, target_currency)
            if applied
            else None,
            settled_currency_id=target_currency if applied else None,
            rate_fetch=applied.fetch if applied else None,
            suggested_rate=applied.suggested if applied else None,
            applied_rate=applied.rate if applied else None,
            rate_manual=applied.manual if applied else False,
            replaces=allocation,
            created_by_user_id=ctx.actor.user_id,
            created_at=timezone.now(),
        )
        orders.record_control_versions(ctx, new_order, "payment")
    ctx.audit(
        "payment.relinked",
        object_type="money_movement",
        object_id=movement.id,
        before={"order": str(allocation.order_id), "amount": allocation.amount},
        after={"order": str(new_order.id) if new_order else None, "reason": reason},
    )
    return replacement


@dataclass(frozen=True)
class Share:
    amount: Decimal
    vehicle_id: UUID | None = None
    order_id: UUID | None = None


def set_shares(
    ctx: CommandContext, company_id: UUID, movement_id: UUID, shares: list[Share]
) -> list[ExpenseShare]:
    """Splits one expense between vehicles and orders by amounts the employee gives; the parts add
    up to the expense. No automatic formulas (§ 17.3.3). An empty list removes the split. The
    location of the result stays by § 17.3.1."""
    movement = _locked(company_id, movement_id)
    if movement.direction != MoneyMovement.Direction.OUT or movement.kind not in (
        MoneyMovement.Kind.MANUAL,
        MoneyMovement.Kind.SUPPLIER,
    ):
        raise errors.business_rule("RECORD_NOT_EXPENSE")
    if MoneyMovement.objects.filter(reverses=movement).exists():
        raise errors.business_rule("RECORD_REVERSED")
    if shares and sum((s.amount for s in shares), ZERO) != movement.amount:
        raise errors.validation_error(
            "shares", code="SHARES_SUM_MISMATCH", amount=str(movement.amount)
        )
    for index, share in enumerate(shares):
        if (share.vehicle_id is None) == (share.order_id is None):
            raise errors.validation_error(f"shares[{index}]", code="SHARE_OBJECT_REQUIRED")
        if (
            share.vehicle_id
            and not VehicleCard.objects.filter(company_id=company_id, pk=share.vehicle_id).exists()
        ):
            raise errors.validation_error(f"shares[{index}]", code="VEHICLE_INVALID")
        if (
            share.order_id
            and not Order.objects.filter(company_id=company_id, pk=share.order_id).exists()
        ):
            raise errors.validation_error(f"shares[{index}]", code="ORDER_INVALID")
    previous = list(movement.shares.filter(state=ExpenseShare.State.ACTIVE))
    for old in previous:
        old.state = ExpenseShare.State.CANCELLED
        old.save(update_fields=["state"])
    now = timezone.now()
    created = [
        ExpenseShare.objects.create(
            company_id=company_id,
            money_movement=movement,
            vehicle_id=share.vehicle_id,
            order_id=share.order_id,
            amount=share.amount,
            created_by_user_id=ctx.actor.user_id,
            created_at=now,
        )
        for share in shares
    ]

    def brief(items: list[ExpenseShare]) -> list[dict[str, Any]]:
        return [
            {
                "vehicle": str(s.vehicle_id) if s.vehicle_id else None,
                "order": str(s.order_id) if s.order_id else None,
                "amount": s.amount,
            }
            for s in items
        ]

    ctx.audit(
        "expense.split",
        object_type="money_movement",
        object_id=movement.id,
        before={"shares": brief(previous)},
        after={"shares": brief(created)},
    )
    return created


def change_client(
    ctx: CommandContext, company_id: UUID, movement_id: UUID, client_id: UUID, reason: str
) -> PaymentClientChange:
    """Corrects a mistakenly named client of a payment (§ 17.2.12): a separate action with the
    finance right and a reason; the previous client stays in history. The payment is first
    unlinked from orders, and the money must still be the previous client's available funds —
    this is fixing a mistake, not giving one client's money to another."""
    movement = _locked(company_id, movement_id)
    if movement.kind != MoneyMovement.Kind.PAYMENT or movement.state != MoneyMovement.State.POSTED:
        raise errors.business_rule("RECORD_NOT_PAYMENT")
    if MoneyMovement.objects.filter(reverses=movement).exists():
        raise errors.business_rule("RECORD_REVERSED")
    if movement.allocations.filter(state=PaymentAllocation.State.ACTIVE).exists():
        raise errors.business_rule("RECORD_ALLOCATED")
    if DepositEntry.objects.filter(money_movement=movement).exists():
        raise errors.business_rule("RECORD_HAS_DEPOSIT")
    previous = settlements.client_of(movement)
    if previous is None or previous == client_id:
        raise errors.validation_error("client_id", code="CLIENT_SAME")
    for locked in sorted([previous, client_id]):
        settlements.lock_client(company_id, locked)
    available = settlements.client_figures(company_id, previous).available.get(
        movement.currency_id, ZERO
    )
    if available < movement.amount:
        raise errors.business_rule("CLIENT_FUNDS_USED", available=str(available))
    change = PaymentClientChange.objects.create(
        company_id=company_id,
        money_movement=movement,
        previous_client_id=previous,
        client_id=client_id,
        reason=reason,
        created_by_user_id=ctx.actor.user_id,
        created_at=timezone.now(),
    )
    ctx.audit(
        "payment.client_changed",
        object_type="money_movement",
        object_id=movement.id,
        before={"client": str(previous)},
        after={"client": str(client_id), "reason": reason},
    )
    return change
