"""Who sees an inspection (§ 10.3.7, § 23.4.1). Used by the views of this module and by other
modules through their service functions (an act of inspection in the documents, В-8.3).

TODO(spec): there is no separate right to view inspections. PROPOSAL: they are seen with any right
of inspections or «View vehicles» in the vehicle's location, and those of a rental or a service
also with «View rentals» or «View service».
"""

from uuid import UUID

from c4rsys.companies import access
from c4rsys.inspections.models import Inspection
from c4rsys.platform.commands import Actor

VIEW_RIGHTS = (
    "inspection.create",
    "inspection.conduct",
    "inspection.cancel",
    "inspection.terminate",
    "inspection.invalidate",
    "vehicle.view",
)


def _has(actor: Actor, permission: str, location_id: UUID | None) -> bool:
    return access.RightsResolver().has_permission(actor, permission, location_id=location_id)


def _location(inspection: Inspection) -> UUID | None:
    return inspection.vehicle_card.accounting_location_id or inspection.place_location_id


def can_view(actor: Actor, inspection: Inspection) -> bool:
    location_id = _location(inspection)
    if any(_has(actor, p, location_id) for p in VIEW_RIGHTS):
        return True
    if inspection.application == Inspection.Application.RENTAL:
        return _has(actor, "rental.view", location_id)
    if inspection.application == Inspection.Application.SERVICE:
        return _has(actor, "service.view", location_id)
    return False
