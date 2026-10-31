"""What the finances need to know of rentals: the location and the vehicle each charge belongs to
(ТЗ § 17.3.1, § 13.4.14). A rental belongs to its issue location; a return elsewhere does not
move it. A standalone additional service belongs to its own location of the service; a linked one
to the rental."""

from uuid import UUID

from c4rsys.finance.attribution import Place
from c4rsys.rentals.models import ExtraServiceLine, Rental, RentalManualCharge


def _rental_place(r: Rental) -> Place:
    return Place(r.issue_location_id, r.vehicle_card_id)


def rentals(company_id: UUID, ids: list[UUID]) -> dict[UUID, Place]:
    return {
        r.id: _rental_place(r) for r in Rental.objects.filter(company_id=company_id, pk__in=ids)
    }


def manual_charges(company_id: UUID, ids: list[UUID]) -> dict[UUID, Place]:
    return {
        m.id: _rental_place(m.rental)
        for m in RentalManualCharge.objects.filter(
            company_id=company_id, pk__in=ids
        ).select_related("rental")
    }


def service_lines(company_id: UUID, ids: list[UUID]) -> dict[UUID, Place]:
    found: dict[UUID, Place] = {}
    for line in ExtraServiceLine.objects.filter(company_id=company_id, pk__in=ids).select_related(
        "rental"
    ):
        if line.rental is not None:
            found[line.id] = _rental_place(line.rental)
        else:
            found[line.id] = Place(line.location_id, None)
    return found
