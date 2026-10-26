"""Set-off of the client's available funds against a debt (ТЗ § 17.2.10, § 17.2.13, § 17.2.15,
§ 17.2.16).

The employee chooses the order (or an initial debt) and the amount: all available funds or part
of them, not above the debt. No order is chosen automatically. A set-off lowers the available
funds and the debt by the same amount, the settlement balance does not change, and no money is
received again. A correction cancels the set-off and creates a new one that names it; the
cancelled one stays in history. Cancelling or lowering a set-off of a closed order that leaves it
with a debt needs an explicit confirmation of the shown debt.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from c4rsys.finance import charges, rates, settlements
from c4rsys.finance.models import Offset, SettlementEntry
from c4rsys.orders import services as orders
from c4rsys.orders.models import Order
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.platform.models import Currency
from c4rsys.platform.money import round_money

ZERO = Decimal(0)


@dataclass(frozen=True)
class NewOffset:
    client_id: UUID
    amount: Decimal  # taken from the funds, in their currency
    currency: str
    order_id: UUID | None = None
    opening_id: UUID | None = None
    rate: Decimal | None = None
    comment: str = ""


def paid_for_order(company_id: UUID, order_id: UUID) -> dict[str, Decimal]:
    """Provider for orders: active set-offs count as paid (§ 12.2.4: «paid / set off»)."""
    rows = (
        Offset.objects.filter(company_id=company_id, order_id=order_id, state=Offset.State.ACTIVE)
        .annotate(
            order_currency=Coalesce("settled_currency_id", "currency_id"),
            settled=Coalesce("settled_amount", "amount"),
        )
        .values("order_currency")
        .annotate(total=Sum("settled"))
    )
    return {r["order_currency"]: r["total"] for r in rows}


def history_for_order(company_id: UUID, order_id: UUID) -> list[tuple[str, str]]:
    ids = Offset.objects.filter(company_id=company_id, order_id=order_id).values_list(
        "id", flat=True
    )
    return [("finance_offset", str(i)) for i in ids]


def snapshot(offset: Offset) -> dict[str, Any]:
    return {
        "client": str(offset.client_id),
        "order": str(offset.order_id) if offset.order_id else None,
        "opening": str(offset.opening_id) if offset.opening_id else None,
        "amount": offset.amount,
        "currency": offset.currency_id,
        "settled_amount": offset.settled_amount,
        "settled_currency": offset.settled_currency_id,
        "applied_rate": offset.applied_rate,
        "suggested_rate": offset.suggested_rate,
        "rate_manual": offset.rate_manual,
        "state": offset.state,
        "replaces": str(offset.replaces_id) if offset.replaces_id else None,
        "comment": offset.comment,
    }


def _target(
    company_id: UUID, data: NewOffset
) -> tuple[Order | None, SettlementEntry | None, str, Decimal]:
    """The order or the initial debt, its currency and what it still owes."""
    if (data.order_id is None) == (data.opening_id is None):
        raise errors.validation_error("order_id", code="OFFSET_TARGET_REQUIRED")
    if data.order_id is not None:
        order = (
            Order.objects.select_for_update()
            .filter(company_id=company_id, pk=data.order_id)
            .first()
        )
        if order is None:
            raise errors.not_found()
        if order.client_id != data.client_id:
            raise errors.business_rule("ORDER_CLIENT_MISMATCH", field="order_id")
        if order.state == Order.State.CANCELLED:
            raise errors.business_rule("ORDER_CANCELLED", field="order_id")
        charged = set(charges.charged_for_order(company_id, order.id))
        if len(charged) != 1:
            # Nothing to set off without a charge; several currencies are not described.
            raise errors.business_rule("ORDER_NOTHING_TO_PAY", field="order_id")
        currency = charged.pop()
        money = orders.amounts_of(order).get(currency)
        debt = (money.charged - money.paid) if money else ZERO
        return order, None, currency, debt
    if data.opening_id is None:  # checked above; keeps the type narrow
        raise errors.validation_error("opening_id", code="OFFSET_TARGET_REQUIRED")
    entry = (
        SettlementEntry.objects.select_for_update()
        .filter(
            company_id=company_id,
            pk=data.opening_id,
            client_id=data.client_id,
            kind=SettlementEntry.Kind.OPENING,
            debtor=SettlementEntry.Debtor.PARTY,
        )
        .first()
    )
    if entry is None:
        raise errors.not_found()
    return None, entry, entry.currency_id, settlements.opening_left(entry)


def create(
    ctx: CommandContext,
    company_id: UUID,
    data: NewOffset,
    *,
    may_override_rate: bool,
    replaces: Offset | None = None,
) -> Offset:
    settlements.lock_client(company_id, data.client_id)
    order, opening, currency, debt = _target(company_id, data)
    available = settlements.client_figures(company_id, data.client_id).available.get(
        data.currency, ZERO
    )
    places = Currency.objects.get(code=data.currency).minor_units
    if data.amount > available:
        raise errors.business_rule(
            "OFFSET_EXCEEDS_AVAILABLE",
            field="amount",
            available=str(round_money(max(available, ZERO), places)),
            currency=data.currency,
        )
    applied = None
    settled = data.amount
    if currency != data.currency:
        applied = rates.apply(data.currency, currency, data.rate, may_override=may_override_rate)
        settled = rates.convert(data.amount, applied.rate, currency)
    elif data.rate is not None and data.rate != 1:
        raise errors.validation_error("rate", code="RATE_SAME_CURRENCY")
    if settled > debt:
        debt_places = Currency.objects.get(code=currency).minor_units
        raise errors.business_rule(
            "OFFSET_EXCEEDS_DEBT",
            field="amount",
            debt=str(round_money(max(debt, ZERO), debt_places)),
            currency=currency,
        )
    offset = Offset.objects.create(
        company_id=company_id,
        client_id=data.client_id,
        order=order,
        opening=opening,
        amount=data.amount,
        currency_id=data.currency,
        settled_amount=settled if applied else None,
        settled_currency_id=currency if applied else None,
        rate_fetch=applied.fetch if applied else None,
        suggested_rate=applied.suggested if applied else None,
        applied_rate=applied.rate if applied else None,
        rate_manual=applied.manual if applied else False,
        replaces=replaces,
        comment=data.comment,
        created_by_user_id=ctx.actor.user_id,
        created_at=timezone.now(),
    )
    if order is not None:
        orders.record_control_versions(ctx, order, "payment")
    ctx.audit(
        "offset.corrected" if replaces else "offset.created",
        object_type="finance_offset",
        object_id=offset.id,
        before=snapshot(replaces) if replaces else None,
        after=snapshot(offset),
    )
    return offset


def _debt_after_cancel(company_id: UUID, offset: Offset) -> Decimal:
    """The debt a closed order would have without this set-off (§ 17.2.16)."""
    if offset.order is None or offset.order.state != Order.State.CLOSED:
        return ZERO
    currency = offset.settled_currency_id or offset.currency_id
    money = orders.amounts_of(offset.order).get(currency)
    if money is None:
        return ZERO
    settled = offset.settled_amount if offset.settled_amount is not None else offset.amount
    return max(money.charged - (money.paid - settled), ZERO)


def cancel(
    ctx: CommandContext,
    company_id: UUID,
    offset_id: UUID,
    *,
    confirmed_debt: Decimal | None,
    comment: str = "",
    replacing: bool = False,
) -> Offset:
    """Cancels the set-off; the money received earlier stays (§ 17.2.15). If a closed order is
    left with a debt, the employee confirms the shown amount first (§ 17.2.16).

    TODO(spec): the notification of the employees who control debts (§ 17.2.16) comes with the
    notifications module."""
    offset = (
        Offset.objects.select_for_update(of=("self",))
        .select_related("order")
        .filter(company_id=company_id, pk=offset_id)
        .first()
    )
    if offset is None:
        raise errors.not_found()
    if offset.state != Offset.State.ACTIVE:
        raise errors.business_rule("OFFSET_CANCELLED")
    settlements.lock_client(company_id, offset.client_id)
    debt = _debt_after_cancel(company_id, offset)
    if debt > 0 and not replacing and confirmed_debt != debt:
        currency = offset.settled_currency_id or offset.currency_id
        places = Currency.objects.get(code=currency).minor_units
        raise errors.business_rule(
            "DEBT_WILL_APPEAR", debt=str(round_money(debt, places)), currency=currency
        )
    before = snapshot(offset)
    offset.state = Offset.State.CANCELLED
    offset.cancelled_at = timezone.now()
    offset.cancelled_by_user_id = ctx.actor.user_id
    offset.save(update_fields=["state", "cancelled_at", "cancelled_by_user_id"])
    if offset.order is not None:
        orders.record_control_versions(ctx, offset.order, "payment")
    if not replacing:
        ctx.audit(
            "offset.cancelled",
            object_type="finance_offset",
            object_id=offset.id,
            before=before,
            after={"state": offset.state, "comment": comment, "debt_after": debt},
        )
    return offset


def correct(
    ctx: CommandContext,
    company_id: UUID,
    offset_id: UUID,
    amount: Decimal,
    rate: Decimal | None,
    *,
    confirmed_debt: Decimal | None,
    may_override_rate: bool,
    comment: str = "",
) -> Offset:
    """A new amount for the same order: the old set-off is cancelled and a new one names it.
    A smaller set-off of a closed order also needs the shown debt confirmed (§ 17.2.16)."""
    old = Offset.objects.select_related("order").filter(company_id=company_id, pk=offset_id).first()
    if old is None:
        raise errors.not_found()
    old_settled = old.settled_amount if old.settled_amount is not None else old.amount
    cancelled = cancel(ctx, company_id, offset_id, confirmed_debt=None, replacing=True)
    new = NewOffset(
        client_id=old.client_id,
        amount=amount,
        currency=old.currency_id,
        order_id=old.order_id,
        opening_id=old.opening_id,
        rate=rate,
        comment=comment,
    )
    offset = create(ctx, company_id, new, may_override_rate=may_override_rate, replaces=cancelled)
    new_settled = offset.settled_amount if offset.settled_amount is not None else offset.amount
    if (
        old.order is not None
        and old.order.state == Order.State.CLOSED
        and new_settled < old_settled
    ):
        currency = offset.settled_currency_id or offset.currency_id
        money = orders.amounts_of(old.order).get(currency)
        debt = max(money.charged - money.paid, ZERO) if money else ZERO
        if debt > 0 and confirmed_debt != debt:
            places = Currency.objects.get(code=currency).minor_units
            raise errors.business_rule(
                "DEBT_WILL_APPEAR", debt=str(round_money(debt, places)), currency=currency
            )
    return offset
