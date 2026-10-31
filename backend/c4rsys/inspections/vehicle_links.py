"""What inspections give the owner of a managed vehicle (ТЗ § 8.3.15, § 10.5.2, § 10.6.1): the
saved inspections, the damages and the fuel readings of this company. Photos are not passed yet
(TODO(spec): which photos are «available», § 8.3.15). Inspections stay in the company on a change
of the owner (§ 10.6.1): only the confirmed management passes them, by its category."""

from datetime import datetime
from typing import Any
from uuid import UUID

from c4rsys.inspections.models import Damage, Inspection


def _saved(company_id: UUID, card_id: UUID, until: datetime | None) -> Any:
    query = Inspection.objects.filter(
        company_id=company_id, vehicle_card_id=card_id, state=Inspection.State.SAVED
    )
    if until is not None:
        query = query.filter(performed_at__lte=until)
    return query.order_by("-performed_at")


def inspections_for_owner(company_id: UUID, card_id: UUID, until: datetime | None) -> list[Any]:
    return [
        {
            "at": i.performed_at,
            "kind": i.kind,
            "odometer": str(i.odometer_value) if i.odometer_value is not None else None,
            "unit": i.odometer_unit,
            "fuel": str(i.fuel_value) if i.fuel_value is not None else None,
        }
        for i in _saved(company_id, card_id, until)
    ]


def fuel_for_owner(company_id: UUID, card_id: UUID, until: datetime | None) -> list[Any]:
    return [
        {"at": i.performed_at, "kind": "fuel", "fuel": str(i.fuel_value)}
        for i in _saved(company_id, card_id, until)
        if i.fuel_value is not None
    ]


def damages_for_owner(company_id: UUID, card_id: UUID, until: datetime | None) -> list[Any]:
    """§ 10.5.2: the event of the global history with its date, mileage, zone and state; an
    erroneous record is shown with its state, it proves nothing."""
    query = Damage.objects.filter(company_id=company_id, vehicle_card_id=card_id)
    if until is not None:
        query = query.filter(performed_at__lte=until)
    return [
        {
            "at": d.performed_at,
            "description": d.description,
            "zone": d.zone_text,
            "odometer": str(d.odometer_value) if d.odometer_value is not None else None,
            "unit": d.odometer_unit,
            "state": d.state,
        }
        for d in query.order_by("-performed_at")
    ]
