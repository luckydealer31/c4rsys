"""Completion of the rental formalization (ТЗ § 14.5.6–14.5.7, § 10.3.3, § 14.3.6).

Formalization is complete when the vehicle is returned, the return inspection is saved with the
odometer and fuel, the required documents are present and inspection conflicts are settled: a
required check not done has a recorded reason (§ 10.3.3) and every new damage attributed to the
rental has its estimate charged (§ 10.5.5). Debt does not prevent completion (§ 14.5.7).
"""

from uuid import UUID

from django.utils import timezone

from c4rsys.documents import requirements as document_requirements
from c4rsys.fleet import services as fleet
from c4rsys.inspections import services as inspections
from c4rsys.inspections.models import Inspection
from c4rsys.orders import services as orders
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.rentals.models import Rental


def missing_conditions(rental: Rental) -> list[str]:
    """Unmet conditions of the formalization of a returned rental."""
    missing = []
    for kind in (Inspection.Kind.ISSUE, Inspection.Kind.RETURN):
        record = inspections.readings_of_rental(rental.id, kind)
        if (
            record is None
            or record.state != Inspection.State.SAVED
            or record.odometer_value is None
            or record.fuel_value is None
        ):
            missing.append(f"{kind}_inspection_not_saved")
    # Documents of the stage «at the return» (§ 18.4.1; decision Р-8).
    stage = (document_requirements.Stage.AT_RETURN,)
    if document_requirements.unmet_for_rental(rental.company_id, rental.id, stage):
        missing.append("return_documents_missing")
    missing += inspections.closing_conditions(rental.id)
    return missing


def complete(
    ctx: CommandContext, company_id: UUID, rental_id: UUID, expected_version: int
) -> Rental:
    found = Rental.objects.filter(company_id=company_id, pk=rental_id).first()
    if found is None:
        raise errors.not_found()
    fleet.lock_card(company_id, found.vehicle_card_id)  # lock order: vehicle → rental
    rental = Rental.objects.select_for_update().get(pk=found.pk)
    if rental.version != expected_version:
        raise errors.version_conflict()
    orders.ensure_open(rental.company_id, rental.order_id)
    if rental.state != Rental.State.RETURNED:
        raise errors.business_rule("RENTAL_NOT_RETURNED", state=rental.state)
    if rental.formalization_completed_at is not None:
        raise errors.business_rule("FORMALIZATION_ALREADY_COMPLETED")
    missing = missing_conditions(rental)
    if missing:
        raise errors.business_rule("FORMALIZATION_INCOMPLETE", missing=missing)
    rental.formalization_completed_at = timezone.now()
    rental.version += 1
    rental.save(update_fields=["formalization_completed_at", "version", "updated_at"])
    ctx.audit(
        "rental.formalization_completed",
        object_type="rental",
        object_id=rental.id,
        after={"formalization_completed_at": rental.formalization_completed_at},
    )
    return rental
