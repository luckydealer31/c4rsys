"""Bookings and rentals in the single search: by the booking number or the id (ТЗ § 2.8.2).
A rental is found only with «View rentals» in its issue location."""

from uuid import UUID

from django.db.models import Q

from c4rsys.companies import access
from c4rsys.platform.commands import Actor
from c4rsys.platform.search import Hit, Query
from c4rsys.rentals import rentals
from c4rsys.rentals.models import Rental


def provider(actor: Actor, company_id: UUID, query: Query, limit: int) -> list[Hit]:
    condition = Q(pk__in=[])
    if query.number is not None:
        condition |= Q(booking_number=query.number)
    if query.uuid is not None:
        condition |= Q(pk=query.uuid)
    resolver = access.RightsResolver()
    hits = []
    found = (
        rentals.rentals_of(company_id)
        .filter(condition)
        .exclude(state=Rental.State.PROPOSED)
        .select_related("vehicle_card")
    )
    for r in found[: limit * 2]:
        if not resolver.has_permission(actor, "rental.view", location_id=r.issue_location_id):
            continue
        label = str(r.booking_number) if r.booking_number else ""
        hits.append(
            Hit(
                "rental",
                str(r.id),
                label,
                r.vehicle_card.name,
                exact=True,
                extra={"state": r.state},
            )
        )
    return hits[:limit]
