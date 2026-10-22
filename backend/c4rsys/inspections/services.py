"""Creating inspection records (ТЗ § 10.1.4–10.1.6, § 14.3.1)."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from django.utils import timezone

from c4rsys.fleet.models import VehicleCard
from c4rsys.inspections.models import Inspection
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
