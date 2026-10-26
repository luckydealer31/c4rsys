"""The deposit of a rental (ТЗ § 13.1.6, § 14.3.3, § 14.7, § 6.7.2, § 12.2.4, § 17.2.10).

The requirement comes from the rental's terms. Money for the deposit is a part of a receipt that
the employee names explicitly; it is held apart from the client's available funds, is not income
and does not pay charges by itself. Held money leaves the deposit by a transfer to the client's
settlement balance (automatically by the term counted from the actual return, or earlier by
hand), by a set-off against a debt, or by a refund payout. A part held by a claim is not moved by
the term. All actions but the receipt need the right «Manage deposits» (customer's decision of
26.10.2026); every entry keeps the amount, the author and the time.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db import transaction
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from c4rsys.finance import charges, rates
from c4rsys.finance.models import CashAccount, DepositEntry, MoneyMovement
from c4rsys.orders import services as orders
from c4rsys.orders.models import Order
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.platform.counters import next_number
from c4rsys.platform.models import Currency
from c4rsys.platform.money import round_money

ZERO = Decimal(0)
K = DepositEntry.Kind


def _rentals() -> Any:
    # The rentals module uses finance (charges); it is imported when needed.
    from c4rsys.rentals import rentals

    return rentals


@dataclass
class Figures:
    """The deposit of one rental in its currency (§ 14.7.3)."""

    currency: str
    required: Decimal
    received: Decimal = ZERO
    to_balance: Decimal = ZERO
    offset: Decimal = ZERO
    refunded: Decimal = ZERO
    claimed: Decimal = ZERO
    waived: bool = False
    issued: bool = False
    hold_days: int | None = None
    returned_at: datetime | None = None
    release_at: datetime | None = None

    @property
    def held(self) -> Decimal:
        return self.received - self.to_balance - self.offset - self.refunded

    @property
    def missing(self) -> Decimal:
        return max(self.required - self.received, ZERO)

    @property
    def conflict(self) -> bool:
        """A missing deposit after the vehicle was handed over (§ 14.3.3)."""
        return self.issued and self.missing > 0 and not self.waived

    @property
    def free(self) -> Decimal:
        """Held and not held by a claim: what the term moves to the balance (§ 14.7.1)."""
        return max(self.held - self.claimed, ZERO)


def figures(rental: Any) -> Figures:
    terms = _rentals().deposit_terms(rental)
    result = Figures(
        currency=terms.currency,
        required=terms.amount if terms.required else ZERO,
        issued=terms.issued,
        hold_days=terms.hold_days,
        returned_at=terms.returned_at,
    )
    if terms.returned_at is not None and terms.hold_days is not None:
        result.release_at = terms.returned_at + timedelta(days=terms.hold_days)
    totals: dict[str, Decimal] = {
        row["kind"]: row["total"]
        for row in DepositEntry.objects.filter(rental_id=rental.id)
        .values("kind")
        .annotate(total=Sum("amount"))
    }
    result.received = totals.get(K.RECEIVED, ZERO)
    result.to_balance = totals.get(K.TO_BALANCE, ZERO)
    result.offset = totals.get(K.OFFSET, ZERO)
    result.refunded = totals.get(K.REFUND, ZERO)
    claimed = totals.get(K.HOLD, ZERO) - totals.get(K.RELEASE, ZERO)
    result.claimed = min(max(claimed, ZERO), max(result.held, ZERO))
    result.waived = K.WAIVER in totals
    return result


def payload(rental: Any) -> dict[str, Any]:
    data = figures(rental)
    places = Currency.objects.get(code=data.currency).minor_units

    def money(value: Decimal) -> str:
        return str(round_money(value, places))

    return {
        "currency": data.currency,
        "required": money(data.required),
        "received": money(data.received),
        "held": money(data.held),
        "claimed": money(data.claimed),
        "offset": money(data.offset),
        "refunded": money(data.refunded),
        "to_balance": money(data.to_balance),
        "missing": money(data.missing),
        "conflict": data.conflict,
        "waived": data.waived,
        "hold_days": data.hold_days,
        "release_at": data.release_at.isoformat() if data.release_at else None,
        "entries": [
            {
                "id": str(e.id),
                "kind": e.kind,
                "amount": money(e.amount),
                "money_amount": str(e.money_amount) if e.money_amount is not None else None,
                "money_currency": e.money_currency_id,
                "order_id": str(e.order_id) if e.order_id else None,
                "reason": e.reason,
                "automatic": e.automatic,
                "created_at": e.created_at.isoformat(),
            }
            for e in DepositEntry.objects.filter(rental_id=rental.id).order_by("created_at")
        ],
    }


# --- Effects on the client's settlements (§ 6.7.2, § 17.2.13) -------------------------------


def client_adjustments(company_id: UUID, client_id: UUID) -> dict[str, Decimal]:
    """The deposit part of receipts is not the client's funds; a transfer to the balance makes it
    so; a set-off pays a debt from the deposit. Per currency, added to the balance."""
    result: dict[str, Decimal] = {}
    entries = DepositEntry.objects.filter(company_id=company_id, client_id=client_id)
    for entry in entries.filter(kind=K.RECEIVED):
        currency = entry.money_currency_id or entry.currency_id
        amount = entry.money_amount if entry.money_amount is not None else entry.amount
        result[currency] = result.get(currency, ZERO) - amount
    for entry in entries.filter(kind__in=[K.TO_BALANCE, K.OFFSET]):
        # A set-off pays the order in the order's currency.
        currency = entry.money_currency_id or entry.currency_id
        amount = entry.money_amount if entry.money_amount is not None else entry.amount
        result[currency] = result.get(currency, ZERO) + amount
    return result


def held_by_client(company_id: UUID, client_id: UUID) -> dict[str, Decimal]:
    """The deposit held for the client, shown apart from the balance (§ 6.7.2)."""
    sign: dict[str, int] = {K.RECEIVED: 1, K.TO_BALANCE: -1, K.OFFSET: -1, K.REFUND: -1}
    result: dict[str, Decimal] = {}
    rows = (
        DepositEntry.objects.filter(company_id=company_id, client_id=client_id, kind__in=sign)
        .values("currency_id", "kind")
        .annotate(total=Sum("amount"))
    )
    for row in rows:
        result[row["currency_id"]] = (
            result.get(row["currency_id"], ZERO) + sign[row["kind"]] * row["total"]
        )
    return {c: v for c, v in result.items() if v != 0}


def paid_for_order(company_id: UUID, order_id: UUID) -> dict[str, Decimal]:
    """Provider for orders: set-offs from deposits count as paid (§ 14.7.2)."""
    rows = (
        DepositEntry.objects.filter(company_id=company_id, order_id=order_id, kind=K.OFFSET)
        .annotate(
            order_currency=Coalesce("money_currency_id", "currency_id"),
            settled=Coalesce("money_amount", "amount"),
        )
        .values("order_currency")
        .annotate(total=Sum("settled"))
    )
    return {r["order_currency"]: r["total"] for r in rows}


# --- Receipt (§ 14.7.3) ---------------------------------------------------------------------


@dataclass(frozen=True)
class Part:
    rental_id: UUID
    amount: Decimal  # in the currency of the receipt
    rate: Decimal | None = None


@dataclass(frozen=True)
class Received:
    rental: Any
    part: Part
    amount: Decimal  # in the deposit's currency
    applied: rates.AppliedRate | None


def prepare(
    company_id: UUID, client_id: UUID, currency: str, parts: tuple[Part, ...], *, may_override: bool
) -> list[Received]:
    """Checks the deposit parts of a receipt before the money is recorded."""
    result = []
    for part in sorted(parts, key=lambda p: p.rental_id):
        rental = _rentals().lock_rental(company_id, part.rental_id)
        if rental.client_id != client_id:
            raise errors.business_rule("RENTAL_CLIENT_MISMATCH", rental_id=str(rental.id))
        if rental.state in ("cancelled", "annulled"):
            raise errors.business_rule("RENTAL_CANCELLED", rental_id=str(rental.id))
        terms = _rentals().deposit_terms(rental)
        applied = None
        amount = part.amount
        if terms.currency != currency:
            applied = rates.apply(currency, terms.currency, part.rate, may_override=may_override)
            amount = rates.convert(part.amount, applied.rate, terms.currency)
            if amount <= 0:
                raise errors.validation_error("deposits", code="AMOUNT_TOO_SMALL")
        result.append(Received(rental=rental, part=part, amount=amount, applied=applied))
    return result


def record_received(
    ctx: CommandContext, movement: MoneyMovement, received: list[Received]
) -> list[dict[str, Any]]:
    journal = []
    for item in received:
        applied = item.applied
        DepositEntry.objects.create(
            company_id=movement.company_id,
            rental_id=item.rental.id,
            client_id=item.rental.client_id,
            kind=K.RECEIVED,
            amount=item.amount,
            currency_id=_rentals().deposit_terms(item.rental).currency,
            money_amount=item.part.amount if applied else None,
            money_currency_id=movement.currency_id if applied else None,
            rate_fetch=applied.fetch if applied else None,
            suggested_rate=applied.suggested if applied else None,
            applied_rate=applied.rate if applied else None,
            rate_manual=applied.manual if applied else False,
            money_movement=movement,
            created_by_user_id=ctx.actor.user_id,
            created_at=movement.created_at,
        )
        journal.append(
            {
                "rental": str(item.rental.id),
                "amount": item.part.amount,
                "deposit_amount": item.amount,
                **(rates.snapshot(applied) if applied else {}),
            }
        )
    return journal


# --- Actions with the held deposit (§ 14.3.3, § 14.7.1–14.7.3) -------------------------------


def _entry(
    ctx: CommandContext | None, rental: Any, kind: str, amount: Decimal, **extra: Any
) -> DepositEntry:
    entry = DepositEntry.objects.create(
        company_id=rental.company_id,
        rental_id=rental.id,
        client_id=rental.client_id,
        kind=kind,
        amount=amount,
        currency_id=_rentals().deposit_terms(rental).currency,
        created_by_user_id=ctx.actor.user_id if ctx else None,
        created_at=timezone.now(),
        **extra,
    )
    if ctx is not None:
        ctx.audit(
            f"deposit.{kind}",
            object_type="rental",
            object_id=rental.id,
            after={
                "kind": kind,
                "amount": amount,
                "currency": entry.currency_id,
                "order": str(entry.order_id) if entry.order_id else None,
                "money_movement": str(entry.money_movement_id) if entry.money_movement_id else None,
                "reason": entry.reason,
            },
        )
    return entry


def _too_much(data: Figures, limit: Decimal, code: str) -> errors.ApiError:
    places = Currency.objects.get(code=data.currency).minor_units
    return errors.business_rule(
        code,
        field="amount",
        limit=str(round_money(max(limit, ZERO), places)),
        currency=data.currency,
    )


def to_balance(
    ctx: CommandContext, company_id: UUID, rental_id: UUID, amount: Decimal, reason: str
) -> DepositEntry:
    """Moves held money to the client's settlement balance before the term (§ 14.7.2). It is not
    a new receipt or payout of money."""
    rental = _rentals().lock_rental(company_id, rental_id)
    data = figures(rental)
    if amount > data.held:
        raise _too_much(data, data.held, "DEPOSIT_EXCEEDS_HELD")
    return _entry(ctx, rental, K.TO_BALANCE, amount, reason=reason)


def hold(
    ctx: CommandContext, company_id: UUID, rental_id: UUID, amount: Decimal, reason: str
) -> DepositEntry:
    """Holds a part by a claim with its amount and reason; the term does not move it (§ 14.7.2)."""
    rental = _rentals().lock_rental(company_id, rental_id)
    data = figures(rental)
    if amount > data.free:
        raise _too_much(data, data.free, "DEPOSIT_EXCEEDS_FREE")
    return _entry(ctx, rental, K.HOLD, amount, reason=reason)


def release(
    ctx: CommandContext, company_id: UUID, rental_id: UUID, amount: Decimal, reason: str
) -> DepositEntry:
    """Ends the claim on a part; it is moved by the term again."""
    rental = _rentals().lock_rental(company_id, rental_id)
    data = figures(rental)
    if amount > data.claimed:
        raise _too_much(data, data.claimed, "DEPOSIT_EXCEEDS_CLAIMED")
    return _entry(ctx, rental, K.RELEASE, amount, reason=reason)


def offset(
    ctx: CommandContext,
    company_id: UUID,
    rental_id: UUID,
    order_id: UUID,
    amount: Decimal,
    *,
    rate: Decimal | None = None,
    may_override_rate: bool = False,
) -> DepositEntry:
    """Sets off the held deposit against a debt of an order of the client, not above the debt
    and the held rest (§ 14.7.2–14.7.3); no money is received again."""
    rental = _rentals().lock_rental(company_id, rental_id)
    data = figures(rental)
    if amount > data.held:
        raise _too_much(data, data.held, "DEPOSIT_EXCEEDS_HELD")
    order = Order.objects.select_for_update().filter(company_id=company_id, pk=order_id).first()
    if order is None:
        raise errors.not_found()
    if order.client_id != rental.client_id:
        raise errors.business_rule("ORDER_CLIENT_MISMATCH", field="order_id")
    if order.state == Order.State.CANCELLED:
        raise errors.business_rule("ORDER_CANCELLED", field="order_id")
    charged = set(charges.charged_for_order(company_id, order.id))
    if len(charged) > 1:
        raise errors.business_rule("ORDER_CURRENCIES_MIXED", field="order_id")
    currency = charged.pop() if charged else data.currency
    # A debt in another currency is paid from the deposit by the applied rate (§ 17.1.4).
    applied = None
    settled = amount
    if currency != data.currency:
        applied = rates.apply(data.currency, currency, rate, may_override=may_override_rate)
        settled = rates.convert(amount, applied.rate, currency)
    money = orders.amounts_of(order).get(currency)
    debt = (money.charged - money.paid) if money else ZERO
    if settled > debt:
        places = Currency.objects.get(code=currency).minor_units
        raise errors.business_rule(
            "OFFSET_EXCEEDS_DEBT",
            field="amount",
            debt=str(round_money(max(debt, ZERO), places)),
            currency=currency,
        )
    entry = _entry(
        ctx,
        rental,
        K.OFFSET,
        amount,
        order=order,
        money_amount=settled if applied else None,
        money_currency_id=currency if applied else None,
        rate_fetch=applied.fetch if applied else None,
        suggested_rate=applied.suggested if applied else None,
        applied_rate=applied.rate if applied else None,
        rate_manual=applied.manual if applied else False,
    )
    orders.record_control_versions(ctx, order, "payment")
    return entry


@dataclass(frozen=True)
class Refund:
    rental_id: UUID
    cash_account_id: UUID
    amount: Decimal  # in the deposit's currency
    rate: Decimal | None = None
    vehicle_with_client: bool = False
    note: str = ""


def refund(
    ctx: CommandContext, company_id: UUID, data: Refund, *, may_override_rate: bool
) -> DepositEntry:
    """Pays back the held deposit fully or in part. By default after the vehicle is returned; an
    earlier refund needs an explicit confirmation (customer's decision of 26.10.2026). In another
    currency the account pays the converted amount by the rate of this payout."""
    account = (
        CashAccount.objects.select_for_update()
        .filter(company_id=company_id, pk=data.cash_account_id)
        .first()
    )
    if account is None:
        raise errors.not_found()
    if account.state != CashAccount.State.ACTIVE:
        raise errors.business_rule("CASH_ACCOUNT_ARCHIVED", field="cash_account_id")
    rental = _rentals().lock_rental(company_id, data.rental_id)
    state = figures(rental)
    if data.amount > state.held:
        raise _too_much(state, state.held, "DEPOSIT_EXCEEDS_HELD")
    if rental.state == "active" and not data.vehicle_with_client:
        raise errors.business_rule("DEPOSIT_VEHICLE_NOT_RETURNED")
    applied = None
    paid = data.amount
    if account.currency_id != state.currency:
        applied = rates.apply(
            state.currency, account.currency_id, data.rate, may_override=may_override_rate
        )
        paid = rates.convert(data.amount, applied.rate, account.currency_id)
        if paid <= 0:
            raise errors.validation_error("amount", code="AMOUNT_TOO_SMALL")
    now = timezone.now()
    movement = MoneyMovement.objects.create(
        company_id=company_id,
        number=next_number(company_id, "payment"),
        cash_account=account,
        kind=MoneyMovement.Kind.DEPOSIT,
        direction=MoneyMovement.Direction.OUT,
        amount=paid,
        currency_id=account.currency_id,
        client_id=rental.client_id,
        state=MoneyMovement.State.POSTED,
        method="",
        note=data.note,
        posted_at=now,
        created_by_user_id=ctx.actor.user_id,
        created_at=now,
    )
    return _entry(
        ctx,
        rental,
        K.REFUND,
        data.amount,
        money_movement=movement,
        money_amount=paid if applied else None,
        money_currency_id=account.currency_id if applied else None,
        rate_fetch=applied.fetch if applied else None,
        suggested_rate=applied.suggested if applied else None,
        applied_rate=applied.rate if applied else None,
        rate_manual=applied.manual if applied else False,
        reason=data.note,
    )


def waive(ctx: CommandContext, company_id: UUID, rental_id: UUID, reason: str) -> DepositEntry:
    """Ends the conflict of a missing deposit for this rental: allowed without or with a partial
    deposit. Money received is not changed or moved; the conflict does not come back (§ 14.3.3,
    § 14.7.4)."""
    rental = _rentals().lock_rental(company_id, rental_id)
    data = figures(rental)
    if not data.conflict:
        raise errors.business_rule("DEPOSIT_NO_CONFLICT")
    return _entry(ctx, rental, K.WAIVER, data.missing, reason=reason)


# --- Transfer by the term (§ 14.7.1) --------------------------------------------------------


def release_expired(now: datetime | None = None) -> int:
    """Moves to the balance what is held and not held by a claim once the term after the actual
    return has passed. Run on a schedule; returns how many deposits were moved."""
    moment = now or timezone.now()
    moved = 0
    for candidate in _rentals().returned_with_deposit_term(moment):
        with transaction.atomic():
            moved += _release_one(candidate.company_id, candidate.id, moment)
    return moved


def _release_one(company_id: UUID, rental_id: UUID, moment: datetime) -> int:
    rental = _rentals().lock_rental(company_id, rental_id)
    data = figures(rental)
    if data.release_at is None or data.release_at > moment or data.free <= 0:
        return 0
    DepositEntry.objects.create(
        company_id=rental.company_id,
        rental_id=rental.id,
        client_id=rental.client_id,
        kind=K.TO_BALANCE,
        amount=data.free,
        currency_id=data.currency,
        automatic=True,
        created_at=data.release_at,
    )
    return 1
