"""Changes of an issued rental (ТЗ § 13.6.2–13.6.5, § 14.5.2, § 14.5.8; customer's decisions
П1–П12 of 28.10.2026).

- Extension: one action changes the planned end of an active rental — later, earlier or for the
  first time, never to the current moment or before it. The client's consent is marked with a
  comment on how it was given. The vehicle must be free for the new period; a delay before the
  extension stays in history. The planned period is charged at once (Р16).
- Tariff: an active rental, or a returned one until the formalization is completed, gets another
  active tariff available for its vehicle from the start of the rental, with a new snapshot of the
  catalog. The included mileage follows the new tariff; the required deposit stays as agreed. A
  linked service the new tariff does not allow stays, with a warning.
- Reopening: a rental with completed formalization is reopened with a reason; nothing else
  changes. A reopened rental gets its actual period and return place corrected; its tariff, free
  time and services change by their own actions; then the formalization is completed again.

TODO(spec): «new terms from a given moment» (§ 13.6.2) — postponed (decision П8).
TODO(spec): whether a corrected return location moves the vehicle's location (§ 8.5.4).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from c4rsys.companies import access, locations
from c4rsys.fleet import services as fleet
from c4rsys.fleet.models import VehicleCard
from c4rsys.orders import services as orders
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext
from c4rsys.rentals import billing, bookings, extra_lines, extras, returns, tariffs
from c4rsys.rentals.models import Rental, RentalFreeInterval, Tariff

EXTEND = "rental.extend"
CHANGE_TERMS = "rental.change_terms"
REOPEN = "rental.reopen"


def require_right(actor: Actor, rental: Rental, permission: str) -> None:
    """The rights work in the issue location of the rental."""
    if not access.RightsResolver().has_permission(
        actor, permission, location_id=rental.issue_location_id
    ):
        raise errors.permission_denied(permission)


def _locked(company_id: UUID, rental_id: UUID, expected_version: int) -> Rental:
    found = Rental.objects.filter(company_id=company_id, pk=rental_id).first()
    if found is None:
        raise errors.not_found()
    fleet.lock_card(company_id, found.vehicle_card_id)  # lock order: vehicle → rental
    rental = Rental.objects.select_for_update().get(pk=found.pk)
    if rental.version != expected_version:
        raise errors.version_conflict()
    orders.ensure_open(rental.company_id, rental.order_id)
    return rental


def _bump(rental: Rental) -> None:
    rental.version += 1
    try:
        with transaction.atomic():
            rental.save()
    except IntegrityError as exc:
        raise errors.business_rule("VEHICLE_NOT_AVAILABLE") from exc


def _require_free_time_inside(rental: Rental, start: datetime, end: datetime, field: str) -> None:
    """Free time outside a new period is a conflict settled by hand (§ 13.3.2)."""
    outside = RentalFreeInterval.objects.filter(rental_id=rental.id, end__gt=end) | (
        RentalFreeInterval.objects.filter(rental_id=rental.id, start__lt=start)
    )
    if outside.exists():
        raise errors.business_rule(
            "FREE_INTERVAL_OUTSIDE_RENTAL", field=field, intervals=[str(i.id) for i in outside]
        )
    services_outside = extra_lines.free_outside(rental, end)
    if services_outside:
        raise errors.business_rule(
            "FREE_INTERVAL_OUTSIDE_SERVICE", field=field, intervals=services_outside
        )


def _require_free(rental: Rental, start: datetime, end: datetime) -> None:
    """No other booking or rental of the vehicle in the period (§ 14.5.2, decision П5); the
    exclusion constraint of the table is the final guard."""
    found = bookings.conflicts(rental.vehicle_card_id, start, end, exclude=rental.id)
    if found:
        raise errors.business_rule("VEHICLE_NOT_AVAILABLE", conflicts=found)


# --- Extension (П1–П5) ----------------------------------------------------------------------


def change_planned_end(
    ctx: CommandContext,
    company_id: UUID,
    rental_id: UUID,
    *,
    planned_end: datetime,
    client_consent: bool,
    consent_comment: str,
    expected_version: int,
) -> Rental:
    if not client_consent:
        raise errors.validation_error("client_consent", code="CLIENT_CONSENT_REQUIRED")
    if not consent_comment.strip():
        raise errors.validation_error("consent_comment", code="FIELD_REQUIRED")
    rental = _locked(company_id, rental_id, expected_version)
    if rental.state != Rental.State.ACTIVE or rental.actual_start is None:
        raise errors.business_rule("RENTAL_NOT_ACTIVE", state=rental.state)
    now = timezone.now()
    if planned_end <= now:
        raise errors.validation_error("planned_end", code="PLANNED_END_NOT_FUTURE")
    if planned_end == rental.planned_end:
        raise errors.validation_error("planned_end", code="PLANNED_END_SAME")
    _require_free_time_inside(rental, rental.actual_start, planned_end, "planned_end")
    _require_free(rental, rental.actual_start, planned_end)
    before = {
        "planned_end": rental.planned_end,
        # § 14.5.8, decision П3: a delay before the extension stays in history.
        "overdue": rental.planned_end is not None and rental.planned_end < now,
    }
    rental.planned_end = planned_end
    _bump(rental)
    ctx.audit(
        "rental.planned_end_changed",
        object_type="rental",
        object_id=rental.id,
        before=before,
        after={
            "planned_end": planned_end,
            "client_consent": True,
            "consent_comment": consent_comment.strip(),
        },
    )
    billing.sync(ctx, rental, "planned_end_changed")
    return rental


# --- Tariff (П6, П7, П9) --------------------------------------------------------------------


def _new_snapshot(rental: Rental, tariff_id: UUID) -> tuple[Tariff, dict[str, Any]]:
    card = VehicleCard.objects.get(pk=rental.vehicle_card_id)
    tariff = tariffs.tariffs_for_vehicle(card).filter(pk=tariff_id).first()
    terms = tariffs.vehicle_terms(tariff, card) if tariff else None
    if tariff is None or terms is None:
        raise errors.business_rule("TARIFF_NOT_AVAILABLE", field="tariff_id")
    tariff = Tariff.objects.select_related("currency").get(pk=tariff.pk)
    snapshot = tariffs.rental_snapshot(tariff, terms)
    # Decision П7: the required deposit stays as agreed, in its own currency.
    previous = rental.tariff_snapshot or {}
    deposit = previous.get("vehicle_terms", {}).get("deposit")
    if deposit is not None:
        snapshot["vehicle_terms"]["deposit"] = {
            **deposit,
            "currency": deposit.get("currency") or previous.get("currency"),
        }
    return tariff, snapshot


def change_tariff(
    ctx: CommandContext,
    company_id: UUID,
    rental_id: UUID,
    *,
    tariff_id: UUID,
    expected_version: int,
) -> Rental:
    rental = _locked(company_id, rental_id, expected_version)
    if rental.state not in (Rental.State.ACTIVE, Rental.State.RETURNED):
        raise errors.business_rule("TARIFF_CHANGE_NOT_ALLOWED", state=rental.state)
    if rental.formalization_completed_at is not None:
        raise errors.business_rule("RENTAL_FORMALIZATION_COMPLETED")
    if tariff_id == rental.tariff_id:
        raise errors.validation_error("tariff_id", code="TARIFF_SAME")
    before = {"tariff": str(rental.tariff_id), "tariff_snapshot": rental.tariff_snapshot}
    rental.tariff, rental.tariff_snapshot = _new_snapshot(rental, tariff_id)
    _bump(rental)
    ctx.audit(
        "rental.tariff_changed",
        object_type="rental",
        object_id=rental.id,
        before=before,
        after={"tariff": str(rental.tariff_id), "tariff_snapshot": rental.tariff_snapshot},
    )
    _recalculate(ctx, rental, "tariff_changed")
    return rental


def services_not_allowed(rental: Rental) -> list[str]:
    """Linked services the rental's terms no longer allow (decision П9): they stay, the manager
    unlinks or cancels them by hand."""
    if rental.tariff_id is None:
        return []
    allowed = set(extras.for_rental(rental).values_list("id", flat=True))
    return [
        str(line.id)
        for line in extra_lines.lines_of_rental(rental)
        if line.rental_id == rental.id and line.service_id not in allowed
    ]


def _recalculate(ctx: CommandContext, rental: Rental, reason: str) -> None:
    if rental.state == Rental.State.RETURNED:
        returns.recalculate(ctx, rental, reason=reason)
    else:
        billing.sync(ctx, rental, reason)


# --- Reopening and correction (П11) ---------------------------------------------------------


def reopen(
    ctx: CommandContext, company_id: UUID, rental_id: UUID, *, reason: str, expected_version: int
) -> Rental:
    """§ 13.6.5: the actual start and end stay, the vehicle is not occupied again."""
    if not reason.strip():
        raise errors.validation_error("reason", code="FIELD_REQUIRED")
    rental = _locked(company_id, rental_id, expected_version)
    if rental.state != Rental.State.RETURNED or rental.formalization_completed_at is None:
        raise errors.business_rule("RENTAL_NOT_COMPLETED", state=rental.state)
    before = {"formalization_completed_at": rental.formalization_completed_at}
    rental.formalization_completed_at = None
    rental.reopened_at = timezone.now()
    _bump(rental)
    ctx.audit(
        "rental.reopened",
        object_type="rental",
        object_id=rental.id,
        before=before,
        after={"reopened_at": rental.reopened_at, "reason": reason.strip()},
    )
    return rental


@dataclass(frozen=True)
class Correction:
    actual_start: datetime
    actual_end: datetime
    return_location_id: UUID
    place_kind: str
    place_note: str


def _correction_snapshot(rental: Rental) -> dict[str, Any]:
    return {
        "actual_start": rental.actual_start,
        "actual_end": rental.actual_end,
        "return_location": str(rental.return_location_id) if rental.return_location_id else None,
        "place_kind": rental.return_place_kind,
        "place_note": rental.return_place_note,
    }


def correct(
    ctx: CommandContext,
    company_id: UUID,
    rental_id: UUID,
    correction: Correction,
    expected_version: int,
) -> Rental:
    rental = _locked(company_id, rental_id, expected_version)
    if (
        rental.state != Rental.State.RETURNED
        or rental.formalization_completed_at is not None
        or rental.reopened_at is None
    ):
        raise errors.business_rule("RENTAL_NOT_REOPENED", state=rental.state)
    start, end = correction.actual_start, correction.actual_end
    if end > timezone.now():
        raise errors.business_rule("FUTURE_TIME_NOT_ALLOWED", field="actual_end")
    if end <= start:
        raise errors.validation_error("actual_end", code="PERIOD_INVALID")
    _require_free_time_inside(rental, start, end, "actual_end")
    _require_free(rental, start, end)
    location_id = returns.acceptance_location(
        rental, correction.place_kind, correction.return_location_id
    )
    if location_id != rental.return_location_id:
        locations.require_usable(company_id, location_id, "return_location_id")
    before = _correction_snapshot(rental)
    rental.actual_start, rental.actual_end = start, end
    rental.actual_period = (start, end)
    rental.return_location_id = location_id
    rental.return_place_kind = correction.place_kind
    rental.return_place_note = correction.place_note
    after = _correction_snapshot(rental)
    if after == before:
        raise errors.validation_error("actual_end", code="NOTHING_CHANGED")
    _bump(rental)
    ctx.audit(
        "rental.corrected",
        object_type="rental",
        object_id=rental.id,
        before=before,
        after=after,
    )
    returns.recalculate(ctx, rental, reason="corrected")
    return rental
