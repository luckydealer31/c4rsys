"""Free time of an issued rental (ТЗ § 13.3; customer's decisions Т2–Т4 of 27.10.2026).

A manager enters the start and either the end or a number of tariff units; the end is then
calculated by the tariff (§ 13.3.2). The interval lies fully inside the rental period: from the
actual start to the actual end, before the return to the planned end, and without a planned end
up to now. Overlaps count once. Free time is entered until the formalization is completed; a
booking has no rental period yet. A returned rental is recalculated at once; an active one shows
the new current cost.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from django.utils import timezone

from c4rsys.companies import access
from c4rsys.orders import services as orders
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext
from c4rsys.rentals import pricing, rentals, returns
from c4rsys.rentals.models import Rental, RentalFreeInterval

RIGHTS = ("rental.issue", "rental.accept_return")


def require_right(actor: Actor, rental: Rental) -> None:
    """The rights of working with the rental in its location (§ 13.3.2; decision Т2)."""
    resolver = access.RightsResolver()
    if not any(
        resolver.has_permission(actor, p, location_id=rental.issue_location_id) for p in RIGHTS
    ):
        raise errors.permission_denied(RIGHTS[0])


def _locked(company_id: UUID, rental_id: UUID, expected_version: int) -> Rental:
    rental = Rental.objects.select_for_update().filter(company_id=company_id, pk=rental_id).first()
    if rental is None:
        raise errors.not_found()
    if rental.version != expected_version:
        raise errors.version_conflict()
    if rental.state not in (Rental.State.ACTIVE, Rental.State.RETURNED):
        raise errors.business_rule("FREE_TIME_NOT_ALLOWED", state=rental.state)
    if rental.formalization_completed_at is not None:
        raise errors.business_rule("RENTAL_FORMALIZATION_COMPLETED")
    orders.ensure_open(rental.company_id, rental.order_id)
    return rental


def period_end(rental: Rental, now: datetime) -> datetime:
    """The latest moment free time may reach (decision Т4)."""
    return rental.actual_end or rental.planned_end or now


def _end(rental: Rental, start: datetime, end: datetime | None, units: int | None) -> datetime:
    if (end is None) == (units is None):
        raise errors.validation_error("end", code="FREE_END_OR_UNITS")
    if units is not None:
        try:
            return pricing.advance(rentals.terms_of(rental), start, units)
        except pricing.PricingError as exc:
            raise errors.validation_error("units", code=exc.code) from exc
    if end is None:
        raise RuntimeError("Checked above: the end or the units are given")
    return end


def _check(rental: Rental, start: datetime, end: datetime) -> None:
    if rental.actual_start is None:
        raise RuntimeError("An issued rental has its actual start")
    if end <= start:
        raise errors.validation_error("end", code="PERIOD_INVALID")
    limit = period_end(rental, timezone.now())
    if start < rental.actual_start or end > limit:
        raise errors.business_rule(
            "FREE_INTERVAL_OUTSIDE_RENTAL",
            field="end" if end > limit else "start",
            start=rental.actual_start.isoformat(),
            end=limit.isoformat(),
        )


def _snapshot(item: RentalFreeInterval) -> dict[str, Any]:
    return {
        "start": item.start.isoformat(),
        "end": item.end.isoformat(),
        "units": item.units,
        "comment": item.comment,
    }


def _after(ctx: CommandContext, rental: Rental, action: str, **change: Any) -> None:
    rental.version += 1
    rental.save(update_fields=["version", "updated_at"])
    ctx.audit(action, object_type="rental", object_id=rental.id, **change)
    if rental.state == Rental.State.RETURNED:
        returns.recalculate(ctx, rental, reason="free_time")


def add(
    ctx: CommandContext,
    company_id: UUID,
    rental_id: UUID,
    *,
    start: datetime,
    end: datetime | None,
    units: int | None,
    comment: str,
    expected_version: int,
) -> RentalFreeInterval:
    rental = _locked(company_id, rental_id, expected_version)
    finish = _end(rental, start, end, units)
    _check(rental, start, finish)
    item = RentalFreeInterval.objects.create(
        company_id=company_id,
        rental=rental,
        start=start,
        end=finish,
        units=units,
        comment=comment,
        created_by_user_id=ctx.actor.user_id,
    )
    _after(ctx, rental, "rental.free_time_added", after={"id": str(item.id), **_snapshot(item)})
    return item


def change(
    ctx: CommandContext,
    company_id: UUID,
    rental_id: UUID,
    interval_id: UUID,
    *,
    start: datetime,
    end: datetime | None,
    units: int | None,
    comment: str,
    expected_version: int,
) -> RentalFreeInterval:
    rental = _locked(company_id, rental_id, expected_version)
    item = (
        RentalFreeInterval.objects.select_for_update()
        .filter(rental_id=rental.id, pk=interval_id)
        .first()
    )
    if item is None:
        raise errors.not_found()
    before = _snapshot(item)
    finish = _end(rental, start, end, units)
    _check(rental, start, finish)
    item.start, item.end, item.units, item.comment = start, finish, units, comment
    item.version += 1
    item.save()
    _after(
        ctx,
        rental,
        "rental.free_time_changed",
        before={"id": str(item.id), **before},
        after={"id": str(item.id), **_snapshot(item)},
    )
    return item


def remove(
    ctx: CommandContext, company_id: UUID, rental_id: UUID, interval_id: UUID, expected_version: int
) -> None:
    rental = _locked(company_id, rental_id, expected_version)
    item = (
        RentalFreeInterval.objects.select_for_update()
        .filter(rental_id=rental.id, pk=interval_id)
        .first()
    )
    if item is None:
        raise errors.not_found()
    before = {"id": str(item.id), **_snapshot(item)}
    item.delete()
    _after(ctx, rental, "rental.free_time_removed", before=before)


def outside(rental: Rental, end: datetime) -> list[dict[str, Any]]:
    """Free intervals reaching past a new end of the rental: the change cannot be completed
    until the manager moves or shortens them by hand (§ 13.3.2)."""
    return [
        {"id": str(i.id), **_snapshot(i)}
        for i in RentalFreeInterval.objects.filter(rental_id=rental.id, end__gt=end)
    ]


def payload(rental: Rental) -> list[dict[str, Any]]:
    return [
        {"id": str(i.id), **_snapshot(i), "version": i.version}
        for i in RentalFreeInterval.objects.filter(rental_id=rental.id).order_by("start")
    ]
