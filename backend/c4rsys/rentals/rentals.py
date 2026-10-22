"""Reading rentals and their place in orders (ТЗ § 12.1.9, § 13.6.7)."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from django.db.models import Max, QuerySet
from django.utils import timezone

from c4rsys.orders.services import OperationSummary
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.rentals import pricing
from c4rsys.rentals.models import Rental, RentalCalculation


def rentals_of(company_id: UUID) -> QuerySet[Rental]:
    return Rental.objects.filter(company_id=company_id).order_by("-actual_start")


def get_rental(company_id: UUID, rental_id: UUID) -> Rental:
    rental = rentals_of(company_id).filter(pk=rental_id).first()
    if rental is None:
        raise errors.not_found()
    return rental


def current_calculation(rental: Rental, now: datetime | None = None) -> dict[str, Any]:
    """While the rental is active the end is the current moment and the result is the current,
    not the actual, cost (§ 13.6.7). Free time is not entered at stage 1."""
    end = rental.actual_end or (now or timezone.now())
    terms = pricing.terms_from_snapshot(rental.tariff_snapshot)
    result = pricing.calculate(terms, rental.actual_start, max(end, rental.actual_start))
    return {
        "kind": "actual" if rental.actual_end else "current",
        "end": end.astimezone(UTC).isoformat(),
        "billable_minutes": int(result.billable.total_seconds() // 60),
        "units": result.units,
        "rate": str(result.rate) if result.rate is not None else None,
        "base_amount": str(result.base_amount),
        "currency": result.currency,
    }


def operations_for_order(company_id: UUID, order_id: UUID) -> list[OperationSummary]:
    """Rentals of an order as the order sees them. A rental is finished when it is returned and
    its formalization is completed, or when it is cancelled or annulled (§ 12.1.9, § 14.5.6)."""
    result = []
    for rental in Rental.objects.filter(company_id=company_id, order_id=order_id):
        finished = rental.state in (Rental.State.CANCELLED, Rental.State.ANNULLED) or (
            rental.state == Rental.State.RETURNED and rental.formalization_completed_at is not None
        )
        result.append(
            OperationSummary(
                type="rental",
                id=rental.id,
                state=rental.state,
                location_id=rental.issue_location_id,
                finished=finished,
            )
        )
    return result


def record_control_versions(
    ctx: CommandContext, company_id: UUID, order_id: UUID, reason: str
) -> None:
    """Writer for orders: one control version per rental of the order (§ 13.6.8)."""
    now = timezone.now()
    for rental in Rental.objects.select_for_update().filter(
        company_id=company_id, order_id=order_id
    ):
        if rental.state in (Rental.State.CANCELLED, Rental.State.ANNULLED):
            continue
        end = rental.actual_end or max(now, rental.actual_start)
        terms = pricing.terms_from_snapshot(rental.tariff_snapshot)
        result = pricing.calculate(terms, rental.actual_start, end)
        seq = (
            RentalCalculation.objects.filter(rental=rental).aggregate(m=Max("seq"))["m"] or 0
        ) + 1
        RentalCalculation.objects.create(
            company_id=company_id,
            rental=rental,
            seq=seq,
            reason=reason,
            kind="actual" if rental.actual_end else "current",
            inputs={
                "actual_start": rental.actual_start.isoformat(),
                "end": end.isoformat(),
                "tariff_id": rental.tariff_snapshot.get("tariff_id"),
                "tariff_version": rental.tariff_snapshot.get("tariff_version"),
                "free_intervals": [],
            },
            billable_minutes=int(result.billable.total_seconds() // 60),
            units=result.units,
            rate=result.rate,
            base_amount=result.base_amount,
            currency=result.currency,
            created_by_user_id=ctx.actor.user_id,
            created_at=now,
        )
        ctx.audit(
            "rental.control_version",
            object_type="rental",
            object_id=rental.id,
            after={"seq": seq, "reason": reason, "base_amount": result.base_amount},
        )
