"""Reading rentals and their place in orders (ТЗ § 12.1.9, § 13.6.7)."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from django.db.models import QuerySet
from django.utils import timezone

from c4rsys.orders.services import OperationSummary
from c4rsys.platform import errors
from c4rsys.rentals import pricing
from c4rsys.rentals.models import Rental


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
