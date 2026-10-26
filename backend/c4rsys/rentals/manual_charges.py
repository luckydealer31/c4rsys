"""Manual charges of a rental (ТЗ § 13.5.1; customer's decision Т6 of 27.10.2026).

The difference of fuel or charge is never turned into money by the system: an employee with the
right «Charge manually in a rental» decides and enters a charge — fuel or charge, or another one —
in the currency of the rental's tariff with a reason. It is a charge of the order apart from the
base cost. A mistaken charge is cancelled with a reason by a reversing entry; nothing is deleted.
"""

from decimal import Decimal
from typing import Any
from uuid import UUID

from django.utils import timezone

from c4rsys.finance import charges
from c4rsys.finance.models import ChargeEntry
from c4rsys.orders import services as orders
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.platform.models import Currency
from c4rsys.platform.money import round_money
from c4rsys.rentals.models import Rental, RentalManualCharge

RIGHT = "rental.manual_charge"


def _rental(company_id: UUID, rental_id: UUID, expected_version: int) -> Rental:
    rental = Rental.objects.select_for_update().filter(company_id=company_id, pk=rental_id).first()
    if rental is None:
        raise errors.not_found()
    if rental.version != expected_version:
        raise errors.version_conflict()
    if rental.state not in (Rental.State.ACTIVE, Rental.State.RETURNED):
        raise errors.business_rule("MANUAL_CHARGE_NOT_ALLOWED", state=rental.state)
    if rental.formalization_completed_at is not None:
        raise errors.business_rule("RENTAL_FORMALIZATION_COMPLETED")
    orders.ensure_open(rental.company_id, rental.order_id)
    return rental


def _set(ctx: CommandContext, rental: Rental, item: RentalManualCharge, amount: Decimal) -> None:
    if rental.client_id is None:
        raise RuntimeError("An issued rental has its client")
    charges.set_charge(
        ctx,
        company_id=rental.company_id,
        order_id=rental.order_id,
        client_id=rental.client_id,
        source_type=ChargeEntry.SourceType.RENTAL_MANUAL,
        source_id=item.id,
        amount=amount,
        currency=item.currency_id,
        reason=item.kind if amount else "cancelled",
        calculation={"kind": item.kind, "reason": item.reason, "rental": str(rental.id)},
    )
    rental.version += 1
    rental.save(update_fields=["version", "updated_at"])


def add(
    ctx: CommandContext,
    company_id: UUID,
    rental_id: UUID,
    *,
    kind: str,
    amount: Decimal,
    reason: str,
    expected_version: int,
) -> RentalManualCharge:
    if kind not in RentalManualCharge.Kind.values:
        raise errors.validation_error("kind", code="CHOICE_REQUIRED")
    if not reason.strip():
        raise errors.validation_error("reason", code="FIELD_REQUIRED")
    rental = _rental(company_id, rental_id, expected_version)
    code = (rental.tariff_snapshot or {}).get("currency")
    currency = Currency.objects.filter(code=code).first()
    if currency is None:
        raise RuntimeError("An issued rental has the currency of its tariff")
    amount = round_money(amount, currency.minor_units)
    if amount <= 0:
        raise errors.validation_error("amount", code="AMOUNT_INVALID")
    item = RentalManualCharge.objects.create(
        company_id=company_id,
        rental=rental,
        kind=kind,
        amount=amount,
        currency=currency,
        reason=reason.strip(),
        created_by_user_id=ctx.actor.user_id,
    )
    _set(ctx, rental, item, amount)
    ctx.audit(
        "rental.manual_charge_added",
        object_type="rental",
        object_id=rental.id,
        after=_snapshot(item),
    )
    return item


def cancel(
    ctx: CommandContext,
    company_id: UUID,
    rental_id: UUID,
    charge_id: UUID,
    reason: str,
    expected_version: int,
) -> RentalManualCharge:
    if not reason.strip():
        raise errors.validation_error("reason", code="FIELD_REQUIRED")
    rental = _rental(company_id, rental_id, expected_version)
    item = (
        RentalManualCharge.objects.select_for_update()
        .filter(rental_id=rental.id, pk=charge_id)
        .first()
    )
    if item is None:
        raise errors.not_found()
    if item.cancelled_at is not None:
        raise errors.business_rule("MANUAL_CHARGE_CANCELLED")
    item.cancelled_at = timezone.now()
    item.cancelled_by_user_id = ctx.actor.user_id
    item.cancel_reason = reason.strip()
    item.save(update_fields=["cancelled_at", "cancelled_by_user_id", "cancel_reason"])
    _set(ctx, rental, item, Decimal(0))
    ctx.audit(
        "rental.manual_charge_cancelled",
        object_type="rental",
        object_id=rental.id,
        after=_snapshot(item),
    )
    return item


def _snapshot(item: RentalManualCharge) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "kind": item.kind,
        "amount": str(item.amount),
        "currency": item.currency_id,
        "reason": item.reason,
        "created_at": item.created_at.isoformat(),
        "cancelled_at": item.cancelled_at.isoformat() if item.cancelled_at else None,
        "cancel_reason": item.cancel_reason,
    }


def payload(rental: Rental) -> list[dict[str, Any]]:
    return [
        _snapshot(i)
        for i in RentalManualCharge.objects.filter(rental_id=rental.id).order_by("created_at")
    ]
