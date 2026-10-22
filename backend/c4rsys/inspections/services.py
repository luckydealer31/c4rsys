"""Creating inspection records (ТЗ § 10.1.4–10.1.6, § 14.3.1)."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from django.utils import timezone

from c4rsys.fleet.models import VehicleCard
from c4rsys.inspections.models import Inspection
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext


@dataclass(frozen=True)
class Readings:
    performed_at: datetime
    place_kind: str
    place_location_id: UUID
    place_note: str
    check_status: str
    odometer: Decimal | None
    fuel: Decimal | None


def create_saved(
    ctx: CommandContext,
    card: VehicleCard,
    *,
    kind: str,
    rental_id: UUID | None,
    readings: Readings,
) -> Inspection:
    """A saved inspection inside the operation's command. The performer is the acting employee;
    the author and the moment of entry are stored separately (§ 10.1.5)."""
    if ctx.actor.employment_id is None:
        raise RuntimeError("An inspection is performed by an employee of the company")
    now = timezone.now()
    inspection = Inspection.objects.create(
        company_id=card.company_id,
        vehicle_card=card,
        rental_id=rental_id,
        kind=kind,
        state=Inspection.State.SAVED,
        check_status=readings.check_status,
        performed_at=readings.performed_at,
        place_kind=readings.place_kind,
        place_location_id=readings.place_location_id,
        place_note=readings.place_note,
        performer_employment_id=ctx.actor.employment_id,
        author_user_id=ctx.actor.user_id,
        entered_at=now,
        odometer_value=readings.odometer,
        odometer_unit=card.odometer_unit,
        fuel_value=readings.fuel,
        saved_at=now,
    )
    ctx.audit(
        "inspection.saved",
        object_type="inspection",
        object_id=inspection.id,
        after={
            "kind": kind,
            "rental": str(rental_id) if rental_id else None,
            "vehicle_card": str(card.id),
            "check_status": readings.check_status,
            "performed_at": readings.performed_at,
            "place_kind": readings.place_kind,
            "place_location": str(readings.place_location_id),
            "place_note": readings.place_note,
            "odometer": readings.odometer,
            "odometer_unit": card.odometer_unit,
            "fuel": readings.fuel,
        },
    )
    return inspection


def create_draft(
    ctx: CommandContext,
    card: VehicleCard,
    *,
    kind: str,
    rental_id: UUID | None,
    readings: Readings,
) -> Inspection:
    """An incomplete record: the return is fixed without the readings (§ 14.5.5)."""
    if ctx.actor.employment_id is None:
        raise RuntimeError("An inspection is performed by an employee of the company")
    inspection = Inspection.objects.create(
        company_id=card.company_id,
        vehicle_card=card,
        rental_id=rental_id,
        kind=kind,
        state=Inspection.State.DRAFT,
        check_status=readings.check_status,
        performed_at=readings.performed_at,
        place_kind=readings.place_kind,
        place_location_id=readings.place_location_id,
        place_note=readings.place_note,
        performer_employment_id=ctx.actor.employment_id,
        author_user_id=ctx.actor.user_id,
        entered_at=timezone.now(),
        odometer_value=readings.odometer,
        odometer_unit=card.odometer_unit,
        fuel_value=readings.fuel,
    )
    ctx.audit(
        "inspection.draft_created",
        object_type="inspection",
        object_id=inspection.id,
        after={
            "kind": kind,
            "rental": str(rental_id) if rental_id else None,
            "odometer": readings.odometer,
            "fuel": readings.fuel,
        },
    )
    return inspection


def save_draft(
    ctx: CommandContext,
    inspection: Inspection,
    *,
    odometer: Decimal,
    fuel: Decimal,
    check_status: str,
) -> Inspection:
    """Completes a draft with the readings and saves it; after that it is immutable (§ 10.1.6,
    § 14.5.5). The moment and place of the inspection stay as recorded."""
    if inspection.state != Inspection.State.DRAFT:
        raise errors.business_rule("INSPECTION_NOT_DRAFT")
    before = {"odometer": inspection.odometer_value, "fuel": inspection.fuel_value}
    inspection.odometer_value = odometer
    inspection.fuel_value = fuel
    inspection.check_status = check_status
    inspection.state = Inspection.State.SAVED
    inspection.saved_at = timezone.now()
    inspection.save(
        update_fields=["odometer_value", "fuel_value", "check_status", "state", "saved_at"]
    )
    ctx.audit(
        "inspection.saved",
        object_type="inspection",
        object_id=inspection.id,
        before=before,
        after={"odometer": odometer, "fuel": fuel, "check_status": check_status},
    )
    return inspection


def of_rental(rental_id: UUID) -> list[Inspection]:
    return list(
        Inspection.objects.filter(rental_id=rental_id)
        .exclude(state=Inspection.State.INVALID)
        .order_by("performed_at")
    )
