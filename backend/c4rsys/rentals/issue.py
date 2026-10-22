"""Issue of a vehicle without a booking (ТЗ § 14.2.2, § 14.3.1, § 14.3.6, § 14.4.3–14.4.4).

One command: the order (new or an existing open one of the client), the rental with the tariff
snapshot and the calculation time zone of the issue location, the issue inspection with odometer
and fuel, the odometer reading and the journal. Policies of a new company give warnings, not
refusals (§ 14.3.6).
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from c4rsys.companies import locations
from c4rsys.fleet import services as fleet
from c4rsys.fleet.models import VehicleCard
from c4rsys.inspections import services as inspections
from c4rsys.inspections.models import Inspection
from c4rsys.orders import services as orders
from c4rsys.parties import services as parties
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.rentals import tariffs
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


def issue(ctx: CommandContext, request: IssueRequest) -> tuple[Rental, Inspection, bool]:
    """Returns the rental, its issue inspection and whether a new order was created."""
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
    if request.planned_return_location_id is not None and not locations.location_exists(
        company_id, request.planned_return_location_id
    ):
        raise errors.validation_error("planned_return_location_id", code="LOCATION_UNKNOWN")
    client = parties.get_client(company_id, request.client_id)
    tariff = tariffs.tariffs_for_vehicle(card).filter(pk=request.tariff_id).first()
    terms = tariffs.vehicle_terms(tariff, card) if tariff else None
    if tariff is None or terms is None:
        raise errors.business_rule("TARIFF_NOT_AVAILABLE", field="tariff_id")
    tariff = Tariff.objects.select_related("currency").get(pk=tariff.pk)
    _check_available(card, request.actual_start)

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

    try:
        with transaction.atomic():
            rental = Rental.objects.create(
                company_id=company_id,
                order=order,
                vehicle_card=card,
                client=client,
                tariff=tariff,
                tariff_snapshot=tariffs.rental_snapshot(tariff, terms),
                calc_timezone=location.timezone,
                issue_location_id=location.id,
                issue_place_kind=request.place_kind,
                issue_place_note=request.place_note,
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

    inspection = inspections.create_saved(
        ctx,
        card,
        kind=Inspection.Kind.ISSUE,
        rental_id=rental.id,
        readings=inspections.Readings(
            performed_at=request.actual_start,
            place_kind=request.place_kind,
            place_location_id=location.id,
            place_note=request.place_note,
            check_status=Inspection.CheckStatus.CONDUCTED
            if request.inspection_conducted
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
    ctx.audit(
        "rental.issued",
        object_type="rental",
        object_id=rental.id,
        after={
            "order": str(order.id),
            "order_number": order.number,
            "vehicle_card": str(card.id),
            "client": str(client.id),
            "tariff": str(tariff.id),
            "issue_location": str(location.id),
            "place_kind": request.place_kind,
            "place_note": request.place_note,
            "actual_start": request.actual_start,
            "planned_end": request.planned_end,
            "planned_return_location": str(request.planned_return_location_id)
            if request.planned_return_location_id
            else None,
            "calc_timezone": location.timezone,
            "inspection": str(inspection.id),
        },
    )
    return rental, inspection, created_order
