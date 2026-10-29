"""Issue of a vehicle, at once or by a booking (ТЗ § 14.2.2, § 14.3.1, § 14.3.6, § 14.4).

One command: the order (new or an existing open one of the client), the rental with the tariff
snapshot and the calculation time zone of the issue location, the issue inspection with odometer
and fuel, the odometer reading and the journal. Policies of a new company give warnings, not
refusals (§ 14.3.6).
"""

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from c4rsys.companies import locations
from c4rsys.delivery import services as deliveries
from c4rsys.fleet import services as fleet
from c4rsys.fleet.models import VehicleCard
from c4rsys.inspections import services as inspections
from c4rsys.inspections.models import Inspection
from c4rsys.orders import services as orders
from c4rsys.parties import services as parties
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.rentals import billing, issue_policies, places, tariffs
from c4rsys.rentals.models import Rental, Tariff


@dataclass(frozen=True)
class IssueRequest:
    vehicle_card_id: UUID
    client_id: UUID
    tariff_id: UUID
    issue_location_id: UUID
    place_kind: str
    place_note: str
    actual_start: datetime
    odometer: Decimal
    fuel: Decimal
    inspection_conducted: bool
    planned_end: datetime | None = None
    order_id: UUID | None = None
    planned_return_location_id: UUID | None = None
    # The booking issued by this action (§ 14.4.1); the same row becomes the active rental.
    booking_id: UUID | None = None
    # «Other» place (§ 14.2.1; customer's decision В1).
    place_address: str = ""
    place_lat: Decimal | None = None
    place_lng: Decimal | None = None
    # How unmet strict requirements are passed (§ 14.3.7).
    exception: issue_policies.ExceptionRequest = issue_policies.ExceptionRequest()

    @property
    def place(self) -> places.Place:
        return places.Place(
            self.place_kind, self.place_note, self.place_address, self.place_lat, self.place_lng
        )


@dataclass(frozen=True)
class Issued:
    rental: Rental
    inspection: Inspection
    order_created: bool
    # Policies that warn (§ 14.3.5–14.3.6) and the inspection check left undone (§ 10.3.3).
    warnings: list[dict[str, Any]]


def _check_vehicle(card: VehicleCard, request: IssueRequest) -> None:
    readiness = fleet.rental_readiness(card)
    if not readiness["ready"]:
        raise errors.business_rule("VEHICLE_NOT_READY", missing=readiness["missing"])
    if card.accounting_location_id != request.issue_location_id:
        # § 14.2.2: the current location of the vehicle must be the issue location.
        raise errors.business_rule(
            "VEHICLE_LOCATION_MISMATCH",
            field="issue_location_id",
            vehicle_location=str(card.accounting_location_id),
        )


def _check_available(card: VehicleCard, start: datetime) -> None:
    """No active rental and no returned one ending after the new start (§ 14.4.4, § 14.6.4).
    The exclusion constraint of the table is the final guard against races."""
    conflict = (
        Rental.objects.filter(
            vehicle_card=card,
            state__in=[Rental.State.ACTIVE, Rental.State.RETURNED],
            actual_period__overlap=(start, None),
        )
        .order_by("-actual_start")
        .first()
    )
    if conflict is not None:
        raise errors.business_rule("VEHICLE_NOT_AVAILABLE", conflict_rental=str(conflict.id))


def _check_not_booked(
    card: VehicleCard, start: datetime, end: datetime | None, booking: UUID | None
) -> None:
    """A booking holds its period (§ 14.1.2): an issue without that booking may not take it. With
    no planned end only a booking already running at the start is checked."""
    query = Rental.objects.filter(
        vehicle_card=card, state=Rental.State.BOOKED, planned_end__gt=start
    )
    if booking is not None:
        query = query.exclude(pk=booking)
    query = (
        query.filter(planned_start__lt=end)
        if end is not None
        else query.filter(planned_start__lte=start)
    )
    found = query.first()
    if found is not None:
        raise errors.business_rule(
            "VEHICLE_BOOKED", booking=str(found.id), booking_number=found.booking_number
        )


def issue(ctx: CommandContext, request: IssueRequest) -> Issued:
    company_id = ctx.actor.company_id
    if company_id is None:
        raise RuntimeError("Issue runs in a company")
    now = timezone.now()
    if request.actual_start > now:
        raise errors.business_rule("FUTURE_TIME_NOT_ALLOWED", field="actual_start")
    if request.planned_end is not None and request.planned_end <= request.actual_start:
        raise errors.validation_error("planned_end", code="PERIOD_INVALID")

    card = fleet.lock_card(company_id, request.vehicle_card_id)
    _check_vehicle(card, request)
    location = locations.location_info(company_id, request.issue_location_id)
    if location is None:
        raise errors.validation_error("issue_location_id", code="LOCATION_UNKNOWN")
    if not location.active:
        raise errors.business_rule("LOCATION_NOT_ACTIVE", field="issue_location_id")
    if request.planned_return_location_id is not None:
        locations.require_usable(
            company_id, request.planned_return_location_id, "planned_return_location_id"
        )
    client = parties.get_client(company_id, request.client_id)
    tariff = tariffs.tariffs_for_vehicle(card).filter(pk=request.tariff_id).first()
    terms = tariffs.vehicle_terms(tariff, card) if tariff else None
    if tariff is None or terms is None:
        raise errors.business_rule("TARIFF_NOT_AVAILABLE", field="tariff_id")
    tariff = Tariff.objects.select_related("currency").get(pk=tariff.pk)
    # § 14.6.3 (decision Д-7): the handover completes the delivery of the same order on the way.
    handover_order = (
        Rental.objects.filter(pk=request.booking_id).values_list("order_id", flat=True).first()
        if request.booking_id is not None
        else request.order_id
    )
    if handover_order is not None:
        deliveries.hand_over_on_issue(
            ctx, company_id, handover_order, card.id, request.actual_start
        )
    _check_available(card, request.actual_start)
    _check_not_booked(card, request.actual_start, request.planned_end, request.booking_id)
    # Other operations holding the vehicle, for example a service (customer's decision С-3).
    held = [
        c
        for c in fleet.occupancy(
            card.company_id, card.id, request.actual_start, request.planned_end
        )
        if not c.get("planned")
    ]
    if held:
        raise errors.business_rule("VEHICLE_NOT_AVAILABLE", conflicts=held)
    place = request.place.checked()
    request = replace(
        request,
        place_kind=place.kind,
        place_note=place.note,
        place_address=place.address,
        place_lat=place.lat,
        place_lng=place.lng,
    )

    if request.booking_id is not None:
        return _issue_booking(ctx, request, card, location, client, tariff, terms, now)

    created_order = request.order_id is None
    if request.order_id is not None:
        order = orders.lock_open_order(company_id, request.order_id)
        if order.client_id != client.id:
            raise errors.business_rule("ORDER_CLIENT_MISMATCH", field="order_id")
    else:
        order = orders.create_order(
            ctx,
            company_id=company_id,
            client_id=client.id,
            responsible_employment_id=ctx.actor.employment_id,
        )

    snapshot = tariffs.rental_snapshot(tariff, terms)
    assessment = _assess(company_id, request, location.id, client.id, order.id, snapshot)
    try:
        with transaction.atomic():
            rental = Rental.objects.create(
                company_id=company_id,
                order=order,
                vehicle_card=card,
                client=client,
                tariff=tariff,
                tariff_snapshot=snapshot,
                calc_timezone=location.timezone,
                issue_location_id=location.id,
                issue_place_kind=place.kind,
                issue_place_note=place.note,
                issue_place_address=place.address,
                issue_place_lat=place.lat,
                issue_place_lng=place.lng,
                planned_end=request.planned_end,
                planned_return_location_id=request.planned_return_location_id,
                actual_start=request.actual_start,
                actual_period=(request.actual_start, None),
                state=Rental.State.ACTIVE,
                created_by_user_id=ctx.actor.user_id,
                created_at=now,
            )
    except IntegrityError as exc:
        raise errors.business_rule("VEHICLE_NOT_AVAILABLE") from exc

    billing.sync(ctx, rental, "issued")
    inspection = _after_issue(ctx, request, card, location, rental, assessment)
    ctx.audit(
        "rental.issued",
        object_type="rental",
        object_id=rental.id,
        after=_issued(rental, order, card, client, tariff, location, request, inspection),
    )
    issue_policies.resolve(ctx, rental, assessment, request.exception)
    return Issued(rental, inspection, created_order, _warnings(assessment, request))


def _assess(
    company_id: UUID,
    request: IssueRequest,
    location_id: UUID,
    client_id: UUID,
    order_id: UUID,
    snapshot: dict[str, Any] | None,
    rental_id: UUID | None = None,
) -> issue_policies.Assessment:
    return issue_policies.assess(
        company_id,
        location_id,
        client_id=client_id,
        order_id=order_id,
        snapshot=snapshot,
        start=request.actual_start,
        planned_end=request.planned_end,
        rental_id=rental_id,
    )


def _warnings(assessment: issue_policies.Assessment, request: IssueRequest) -> list[dict[str, Any]]:
    """Warnings of the policies; a required condition check not done is a conflict, not a
    refusal (§ 10.3.3)."""
    result = list(assessment.warnings)
    if assessment.inspection_required and not request.inspection_conducted:
        result.append({"code": "INSPECTION_NOT_DONE"})
    return result


def _issue_booking(
    ctx: CommandContext,
    request: IssueRequest,
    card: VehicleCard,
    location: Any,
    client: Any,
    tariff: Tariff,
    terms: Any,
    now: datetime,
) -> Issued:
    """The issue of a booking: the agreed terms of the booking stay when its tariff is chosen;
    the order of the booking gets its client (§ 12.1.8, § 14.1.6, § 14.4.1)."""
    booking = (
        Rental.objects.select_for_update()
        .filter(company_id=card.company_id, pk=request.booking_id or card.id)
        .first()
    )
    if booking is None:
        raise errors.not_found()
    if booking.state != Rental.State.BOOKED:
        raise errors.business_rule("BOOKING_NOT_ACTIVE")
    if booking.vehicle_card_id != card.id:
        raise errors.business_rule("BOOKING_VEHICLE_MISMATCH", field="vehicle_card_id")
    order = orders.lock_open_order(card.company_id, booking.order_id)
    if booking.tariff_id != tariff.id or not booking.tariff_snapshot:
        booking.tariff_snapshot = tariffs.rental_snapshot(tariff, terms)
    booking.client = client
    booking.tariff = tariff
    booking.calc_timezone = location.timezone
    booking.issue_location_id = location.id
    places.set_issue(booking, request.place)
    booking.planned_end = request.planned_end or booking.planned_end
    request = replace(request, planned_end=booking.planned_end)
    assessment = _assess(
        card.company_id,
        request,
        location.id,
        client.id,
        order.id,
        booking.tariff_snapshot,
        rental_id=booking.id,
    )
    if request.planned_return_location_id is not None:
        booking.planned_return_location_id = request.planned_return_location_id
    booking.actual_start = request.actual_start
    booking.actual_period = (request.actual_start, None)
    booking.auto_cancel_at = None
    booking.state = Rental.State.ACTIVE
    booking.version += 1
    try:
        with transaction.atomic():
            booking.save()
    except IntegrityError as exc:
        raise errors.business_rule("VEHICLE_NOT_AVAILABLE") from exc
    # After the save, so the charges created for the client follow the issued terms (Р25).
    orders.assign_client(ctx, order, client.id)
    billing.sync(ctx, booking, "issued")
    inspection = _after_issue(ctx, request, card, location, booking, assessment)
    ctx.audit(
        "rental.issued",
        object_type="rental",
        object_id=booking.id,
        after={
            **_issued(booking, order, card, client, tariff, location, request, inspection),
            "booking_number": booking.booking_number,
        },
    )
    issue_policies.resolve(ctx, booking, assessment, request.exception)
    return Issued(booking, inspection, False, _warnings(assessment, request))


def _after_issue(
    ctx: CommandContext,
    request: IssueRequest,
    card: VehicleCard,
    location: Any,
    rental: Rental,
    assessment: issue_policies.Assessment,
) -> Inspection:
    """The issue inspection with odometer and fuel and the odometer reading (§ 14.4.3)."""
    inspection = inspections.create_saved(
        ctx,
        card,
        kind=Inspection.Kind.ISSUE,
        rental_id=rental.id,
        readings=inspections.Readings(
            performed_at=request.actual_start,
            place_kind=request.place_kind,
            place_location_id=location.id,
            place_note=request.place.summary(),
            check_status=Inspection.CheckStatus.CONDUCTED
            if request.inspection_conducted
            else Inspection.CheckStatus.REQUIRED_NOT_DONE
            if assessment.inspection_required
            else Inspection.CheckStatus.NOT_REQUIRED,
            odometer=request.odometer,
            fuel=request.fuel,
        ),
    )
    fleet.record_reading(
        ctx,
        card,
        measured_at=request.actual_start,
        value=request.odometer,
        source_type="inspection",
        source_id=inspection.id,
    )
    return inspection


def _issued(
    rental: Rental,
    order: Any,
    card: VehicleCard,
    client: Any,
    tariff: Tariff,
    location: Any,
    request: IssueRequest,
    inspection: Inspection,
) -> dict[str, Any]:
    return {
        "order": str(order.id),
        "order_number": order.number,
        "vehicle_card": str(card.id),
        "client": str(client.id),
        "tariff": str(tariff.id),
        "issue_location": str(location.id),
        "place": request.place.as_dict(),
        "actual_start": request.actual_start,
        "planned_end": request.planned_end,
        "planned_return_location": str(request.planned_return_location_id)
        if request.planned_return_location_id
        else None,
        "calc_timezone": location.timezone,
        "inspection": str(inspection.id),
    }
