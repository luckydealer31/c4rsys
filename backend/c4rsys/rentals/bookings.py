"""Bookings of vehicles (ТЗ § 14.1, § 14.2.2, § 14.6.2, § 14.7.3, § 14.8.1, § 12.1.8).

A booking holds a concrete vehicle for its planned period at once, without prepayment. It can be
saved after a phone call with only the vehicle and the planned start and end; the client, the
tariff, the issue location, a contact and a comment are added later, but the client, the tariff
and the issue location are required before the issue. The order is created with the booking and
may wait for its client. Bookings of one vehicle do not overlap; a late return that touches the
next booking is a conflict shown on that booking, not an automatic change.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from c4rsys.companies import locations
from c4rsys.companies.models import Company
from c4rsys.fleet import services as fleet
from c4rsys.fleet.models import VehicleCard
from c4rsys.orders import services as orders
from c4rsys.parties import services as parties
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.platform.counters import next_number
from c4rsys.rentals import tariffs
from c4rsys.rentals.models import Rental, RentalPolicy, Tariff

BOOKED = Rental.State.BOOKED


# --- Availability (§ 14.6) ------------------------------------------------------------------


def _occupancy_end(rental: Rental, now: datetime) -> datetime | None:
    """Until when an issued rental holds its vehicle: returned — its actual end; active — the
    planned end, or now when it is overdue or has none."""
    if rental.actual_end is not None:
        return rental.actual_end
    if rental.planned_end is not None and rental.planned_end > now:
        return rental.planned_end
    return now


def conflicts(
    card_id: UUID, start: datetime, end: datetime, *, exclude: UUID | None = None
) -> list[dict[str, Any]]:
    """Operations of the vehicle that overlap [start, end): other bookings and issued rentals."""
    now = timezone.now()
    result = []
    query = Rental.objects.filter(vehicle_card_id=card_id).exclude(
        state__in=[Rental.State.CANCELLED, Rental.State.ANNULLED]
    )
    if exclude is not None:
        query = query.exclude(pk=exclude)
    for rental in query.filter(Q(state=BOOKED, planned_end__gt=start, planned_start__lt=end)):
        result.append({"kind": "booking", "id": str(rental.id), "number": rental.booking_number})
    for rental in query.filter(state__in=[Rental.State.ACTIVE, Rental.State.RETURNED]):
        until = _occupancy_end(rental, now)
        if (
            rental.actual_start is not None
            and rental.actual_start < end
            and (until is None or until > start)
        ):
            result.append({"kind": "rental", "id": str(rental.id), "state": rental.state})
    return result


def _require_free(
    card_id: UUID, start: datetime, end: datetime, exclude: UUID | None = None
) -> None:
    found = conflicts(card_id, start, end, exclude=exclude)
    if found:
        raise errors.business_rule("VEHICLE_NOT_AVAILABLE", conflicts=found)


def delay_conflict(booking: Rental) -> dict[str, Any] | None:
    """A late return of the vehicle that touches this booking (§ 14.6.2): the booking stays, the
    vehicle cannot be issued until it is returned; the employee resolves it by hand."""
    if booking.state != BOOKED or booking.planned_start is None:
        return None
    now = timezone.now()
    active = (
        Rental.objects.filter(vehicle_card_id=booking.vehicle_card_id, state=Rental.State.ACTIVE)
        .exclude(pk=booking.id)
        .first()
    )
    if active is None:
        return None
    # The vehicle is late for the booking when the rental is already overdue, is planned to end
    # after the booking starts, or — with no planned end — is still out when the booking starts.
    overdue = active.planned_end is not None and active.planned_end < now
    planned_late = active.planned_end is not None and active.planned_end > booking.planned_start
    still_out = active.planned_end is None and now >= booking.planned_start
    if not (overdue or planned_late or still_out):
        return None
    return {
        "rental": str(active.id),
        "planned_end": active.planned_end.isoformat() if active.planned_end else None,
        "overdue": active.planned_end is not None and active.planned_end < now,
    }


# --- Auto-cancel policy (§ 14.8.1) ----------------------------------------------------------


def policy_for(company_id: UUID, location_id: UUID | None) -> dict[str, Any]:
    """The rule a new booking gets: of its rental location if set, else of the company; off by
    default."""
    rows = {
        p.location_id: p
        for p in RentalPolicy.objects.filter(company_id=company_id).filter(
            Q(location__isnull=True) | Q(location_id__in=[location_id] if location_id else [])
        )
    }
    chosen = rows.get(location_id) if location_id is not None else None
    source = "location"
    if chosen is None:
        chosen = rows.get(None)
        source = "company"
    if chosen is None or not chosen.auto_cancel_enabled:
        return {"enabled": False, "source": source if chosen else "default"}
    return {"enabled": True, "after_minutes": chosen.auto_cancel_after_minutes, "source": source}


def set_policy(
    ctx: CommandContext,
    company_id: UUID,
    location_id: UUID | None,
    enabled: bool,
    after_minutes: int | None,
) -> RentalPolicy:
    if enabled and after_minutes is None:
        raise errors.validation_error("after_minutes", code="FIELD_REQUIRED")
    if location_id is not None:
        locations.require_usable(company_id, location_id, "location_id")
    policy = (
        RentalPolicy.objects.select_for_update()
        .filter(company_id=company_id)
        .filter(Q(location__isnull=True) if location_id is None else Q(location_id=location_id))
        .first()
    )
    before = (
        {"enabled": policy.auto_cancel_enabled, "after_minutes": policy.auto_cancel_after_minutes}
        if policy
        else None
    )
    if policy is None:
        policy = RentalPolicy(company_id=company_id, location_id=location_id, version=0)
    policy.auto_cancel_enabled = enabled
    policy.auto_cancel_after_minutes = after_minutes if enabled else None
    policy.updated_by_user_id = ctx.actor.user_id
    policy.updated_at = timezone.now()
    policy.version += 1
    policy.save()
    ctx.audit(
        "rental.policy_changed",
        object_type="rental_policy",
        object_id=policy.id,
        before=before,
        after={
            "location": str(location_id) if location_id else None,
            "enabled": enabled,
            "after_minutes": policy.auto_cancel_after_minutes,
        },
    )
    return policy


def _auto_cancel_at(rule: dict[str, Any], planned_start: datetime) -> datetime | None:
    if not rule.get("enabled"):
        return None
    return planned_start + timedelta(minutes=int(rule["after_minutes"]))


# --- Commands -------------------------------------------------------------------------------


def snapshot(rental: Rental) -> dict[str, Any]:
    return {
        "booking_number": rental.booking_number,
        "vehicle_card": str(rental.vehicle_card_id),
        "client": str(rental.client_id) if rental.client_id else None,
        "tariff": str(rental.tariff_id) if rental.tariff_id else None,
        "issue_location": str(rental.issue_location_id) if rental.issue_location_id else None,
        "issue_place_kind": rental.issue_place_kind,
        "issue_place_note": rental.issue_place_note,
        "planned_start": rental.planned_start,
        "planned_end": rental.planned_end,
        "planned_return_location": str(rental.planned_return_location_id)
        if rental.planned_return_location_id
        else None,
        "contact": rental.booking_contact,
        "comment": rental.booking_comment,
        "auto_cancel_at": rental.auto_cancel_at,
        "deposit_override": rental.deposit_override,
        "state": rental.state,
    }


@dataclass(frozen=True)
class BookingData:
    vehicle_card_id: UUID
    planned_start: datetime
    planned_end: datetime
    client_id: UUID | None = None
    tariff_id: UUID | None = None
    issue_location_id: UUID | None = None
    place_kind: str = ""
    place_note: str = ""
    planned_return_location_id: UUID | None = None
    contact: str = ""
    comment: str = ""


def _tariff(card: VehicleCard, tariff_id: UUID) -> tuple[Tariff, dict[str, Any]]:
    tariff = tariffs.tariffs_for_vehicle(card).filter(pk=tariff_id).first()
    terms = tariffs.vehicle_terms(tariff, card) if tariff else None
    if tariff is None or terms is None:
        raise errors.business_rule("TARIFF_NOT_AVAILABLE", field="tariff_id")
    tariff = Tariff.objects.select_related("currency").get(pk=tariff.pk)
    return tariff, tariffs.rental_snapshot(tariff, terms)


def _place(company_id: UUID, data: BookingData) -> tuple[Any, str]:
    """The issue location must have rental and be usable (§ 14.2.2); a booking may name another
    location than the vehicle's current one (a warning, not a refusal). Without a location the
    time zone of the company is used until one is chosen."""
    if data.issue_location_id is None:
        company = Company.objects.get(pk=company_id)
        return None, company.timezone or "UTC"
    location = locations.location_info(company_id, data.issue_location_id)
    if location is None:
        raise errors.validation_error("issue_location_id", code="LOCATION_UNKNOWN")
    if not location.active:
        raise errors.business_rule("LOCATION_NOT_ACTIVE", field="issue_location_id")
    if data.place_kind not in ("location", "other"):
        raise errors.validation_error("place_kind")
    if data.place_kind == "other" and not data.place_note.strip():
        raise errors.validation_error("place_note", code="FIELD_REQUIRED")
    return location, location.timezone


def create(ctx: CommandContext, company_id: UUID, data: BookingData) -> Rental:
    if data.planned_end <= data.planned_start:
        raise errors.validation_error("planned_end", code="PERIOD_INVALID")
    card = fleet.lock_card(company_id, data.vehicle_card_id)
    _require_free(card.id, data.planned_start, data.planned_end)
    client = parties.get_client(company_id, data.client_id) if data.client_id else None
    tariff, snap = _tariff(card, data.tariff_id) if data.tariff_id else (None, None)
    location, zone = _place(company_id, data)
    if data.planned_return_location_id is not None:
        locations.require_usable(
            company_id, data.planned_return_location_id, "planned_return_location_id"
        )
    order = orders.create_order(
        ctx,
        company_id=company_id,
        client_id=client.id if client else None,
        responsible_employment_id=ctx.actor.employment_id,
    )
    rule = policy_for(company_id, location.id if location else None)
    now = timezone.now()
    try:
        with transaction.atomic():
            rental = Rental.objects.create(
                company_id=company_id,
                order=order,
                vehicle_card=card,
                client=client,
                tariff=tariff,
                tariff_snapshot=snap,
                calc_timezone=zone,
                issue_location_id=location.id if location else None,
                issue_place_kind=data.place_kind if location else "",
                issue_place_note=data.place_note if location else "",
                planned_start=data.planned_start,
                planned_end=data.planned_end,
                booking_period=(data.planned_start, data.planned_end),
                planned_return_location_id=data.planned_return_location_id,
                booking_number=next_number(company_id, "booking"),
                booking_contact=data.contact,
                booking_comment=data.comment,
                auto_cancel_rule=rule,
                auto_cancel_at=_auto_cancel_at(rule, data.planned_start),
                state=BOOKED,
                created_by_user_id=ctx.actor.user_id,
                created_at=now,
            )
    except IntegrityError as exc:
        raise errors.business_rule("VEHICLE_NOT_AVAILABLE") from exc
    ctx.audit(
        "rental.booked",
        object_type="rental",
        object_id=rental.id,
        after={**snapshot(rental), "order": str(order.id), "order_number": order.number},
    )
    return rental


def _locked_booking(company_id: UUID, rental_id: UUID, expected_version: int) -> Rental:
    rental = Rental.objects.select_for_update().filter(company_id=company_id, pk=rental_id).first()
    if rental is None:
        raise errors.not_found()
    if rental.version != expected_version:
        raise errors.version_conflict()
    if rental.state != BOOKED:
        raise errors.business_rule("BOOKING_NOT_ACTIVE")
    return rental


def _save(ctx: CommandContext, rental: Rental, before: dict[str, Any], action: str) -> Rental:
    rental.version += 1
    try:
        with transaction.atomic():
            rental.save()
    except IntegrityError as exc:
        raise errors.business_rule("VEHICLE_NOT_AVAILABLE") from exc
    ctx.audit(
        action, object_type="rental", object_id=rental.id, before=before, after=snapshot(rental)
    )
    return rental


@dataclass(frozen=True)
class BookingChanges:
    """Only the given fields change (None — not given); `clear_*` empties an optional field."""

    planned_start: datetime | None = None
    planned_end: datetime | None = None
    client_id: UUID | None = None
    tariff_id: UUID | None = None
    issue_location_id: UUID | None = None
    place_kind: str | None = None
    place_note: str | None = None
    planned_return_location_id: UUID | None = None
    contact: str | None = None
    comment: str | None = None


def update(
    ctx: CommandContext,
    company_id: UUID,
    rental_id: UUID,
    changes: BookingChanges,
    expected_version: int,
) -> Rental:
    """Changes a booking with history and an availability check (§ 14.1.9, § 14.6.2). A new start
    keeps the end until it is changed separately; the auto-cancel moment follows the start."""
    rental = _locked_booking(company_id, rental_id, expected_version)
    before = snapshot(rental)
    start = changes.planned_start or rental.planned_start
    end = changes.planned_end or rental.planned_end
    if start is None or end is None or end <= start:
        raise errors.validation_error("planned_end", code="PERIOD_INVALID")
    if (start, end) != (rental.planned_start, rental.planned_end):
        _require_free(rental.vehicle_card_id, start, end, exclude=rental.id)
        if start != rental.planned_start and rental.auto_cancel_rule:
            rental.auto_cancel_at = _auto_cancel_at(rental.auto_cancel_rule, start)
        rental.planned_start, rental.planned_end = start, end
        rental.booking_period = (start, end)
    if changes.client_id is not None and changes.client_id != rental.client_id:
        client = parties.get_client(company_id, changes.client_id)
        order = orders.lock_open_order(company_id, rental.order_id)
        orders.assign_client(ctx, order, client.id)
        rental.client = client
    if changes.tariff_id is not None and changes.tariff_id != rental.tariff_id:
        card = VehicleCard.objects.get(pk=rental.vehicle_card_id)
        rental.tariff, rental.tariff_snapshot = _tariff(card, changes.tariff_id)
    if changes.issue_location_id is not None:
        data = BookingData(
            vehicle_card_id=rental.vehicle_card_id,
            planned_start=start,
            planned_end=end,
            issue_location_id=changes.issue_location_id,
            place_kind=changes.place_kind or rental.issue_place_kind or "location",
            place_note=changes.place_note
            if changes.place_note is not None
            else rental.issue_place_note,
        )
        location, zone = _place(company_id, data)
        rental.issue_location_id = location.id
        rental.calc_timezone = zone
        rental.issue_place_kind = data.place_kind
        rental.issue_place_note = data.place_note
    if changes.planned_return_location_id is not None:
        locations.require_usable(
            company_id, changes.planned_return_location_id, "planned_return_location_id"
        )
        rental.planned_return_location_id = changes.planned_return_location_id
    if changes.contact is not None:
        rental.booking_contact = changes.contact
    if changes.comment is not None:
        rental.booking_comment = changes.comment
    return _save(ctx, rental, before, "rental.booking_changed")


def replace_vehicle(
    ctx: CommandContext,
    company_id: UUID,
    rental_id: UUID,
    vehicle_card_id: UUID,
    tariff_id: UUID | None,
    expected_version: int,
) -> Rental:
    """Another vehicle for an upcoming booking (§ 14.1.6, § 14.6.2). The agreed terms stay when
    the tariff is available for the new vehicle; otherwise the employee chooses new terms
    explicitly — no automatic substitution. The previous terms stay in history."""
    rental = _locked_booking(company_id, rental_id, expected_version)
    if vehicle_card_id == rental.vehicle_card_id:
        raise errors.validation_error("vehicle_card_id", code="VEHICLE_SAME")
    before = snapshot(rental)
    card = fleet.lock_card(company_id, vehicle_card_id)
    if rental.planned_start is None or rental.planned_end is None:  # a booking has both
        raise errors.business_rule("BOOKING_NOT_ACTIVE")
    _require_free(card.id, rental.planned_start, rental.planned_end, exclude=rental.id)
    if tariff_id is not None:
        rental.tariff, rental.tariff_snapshot = _tariff(card, tariff_id)
    elif rental.tariff_id is not None:
        tariff = tariffs.tariffs_for_vehicle(card).filter(pk=rental.tariff_id).first()
        if tariff is None or tariffs.vehicle_terms(tariff, card) is None:
            raise errors.business_rule("TARIFF_NOT_AVAILABLE_FOR_VEHICLE", field="tariff_id")
        # The same tariff keeps the agreed price; its terms for the new vehicle (mileage,
        # deposit) apply.
        snap = dict(rental.tariff_snapshot or {})
        new_snap = tariffs.rental_snapshot(tariff, tariffs.vehicle_terms(tariff, card))  # type: ignore[arg-type]
        snap["vehicle_terms"] = new_snap["vehicle_terms"]
        rental.tariff_snapshot = snap
    rental.vehicle_card = card
    return _save(ctx, rental, before, "rental.booking_vehicle_replaced")


def cancel(
    ctx: CommandContext | None,
    company_id: UUID,
    rental_id: UUID,
    reason: str,
    expected_version: int | None,
    *,
    automatic: bool = False,
) -> Rental:
    """Frees the booked period; the history and payments stay, money is paid back separately
    (§ 14.1.5). The order and its other positions stay (§ 14.8.1)."""
    rental = Rental.objects.select_for_update().filter(company_id=company_id, pk=rental_id).first()
    if rental is None:
        raise errors.not_found()
    if expected_version is not None and rental.version != expected_version:
        raise errors.version_conflict()
    if rental.state != BOOKED:
        raise errors.business_rule("BOOKING_NOT_ACTIVE")
    before = snapshot(rental)
    rental.state = Rental.State.CANCELLED
    rental.cancelled_at = timezone.now()
    rental.cancel_reason = reason
    rental.version += 1
    rental.save()
    if ctx is not None:
        ctx.audit(
            "rental.booking_cancelled",
            object_type="rental",
            object_id=rental.id,
            before=before,
            after={"state": rental.state, "reason": reason, "automatic": automatic},
        )
    return rental


def set_auto_cancel(
    ctx: CommandContext,
    company_id: UUID,
    rental_id: UUID,
    at: datetime | None,
    expected_version: int,
) -> Rental:
    """An authorized employee changes the auto-cancel moment of this booking before it fires;
    empty switches it off for the booking (§ 14.8.1)."""
    rental = _locked_booking(company_id, rental_id, expected_version)
    before = snapshot(rental)
    rental.auto_cancel_at = at
    return _save(ctx, rental, before, "rental.auto_cancel_changed")


def set_deposit(
    ctx: CommandContext,
    company_id: UUID,
    rental_id: UUID,
    required: bool,
    amount: Decimal | None,
    reason: str,
    expected_version: int,
) -> Rental:
    """Changes or cancels the deposit of this booking before the issue with a reason, without
    changing the tariff (§ 14.7.3)."""
    rental = _locked_booking(company_id, rental_id, expected_version)
    if required and (amount is None or amount <= 0):
        raise errors.validation_error("amount", code="AMOUNT_INVALID")
    before = snapshot(rental)
    rental.deposit_override = {
        "required": required,
        "amount": str(amount) if required and amount is not None else None,
        "reason": reason,
    }
    return _save(ctx, rental, before, "rental.deposit_changed")


def cancel_no_shows(now: datetime | None = None) -> int:
    """Cancels bookings whose auto-cancel moment has passed and that are not issued (§ 14.8.1).
    Run on a schedule; returns how many were cancelled.

    TODO(spec): a delivery or other preparation in progress keeps the booking with a conflict —
    with deliveries (part А7)."""
    moment = now or timezone.now()
    cancelled = 0
    for booking in Rental.objects.filter(state=BOOKED, auto_cancel_at__lte=moment).only(
        "id", "company_id"
    ):
        with transaction.atomic():
            locked = Rental.objects.select_for_update().filter(pk=booking.id, state=BOOKED).first()
            if locked is None or locked.auto_cancel_at is None or locked.auto_cancel_at > moment:
                continue
            cancel(None, locked.company_id, locked.id, "no_show", None, automatic=True)
            cancelled += 1
    return cancelled
