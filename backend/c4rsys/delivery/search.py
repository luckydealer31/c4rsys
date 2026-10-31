"""Trips in the single search: by the number or the id (ТЗ § 2.8.2). A trip is found only where
it is visible: «View trips» in its start or end location, or the driver's own trip."""

from uuid import UUID

from django.db.models import Q

from c4rsys.delivery import services
from c4rsys.platform.commands import Actor
from c4rsys.platform.search import Hit, Query


def provider(actor: Actor, company_id: UUID, query: Query, limit: int) -> list[Hit]:
    from c4rsys.delivery.views import _visible

    condition = Q(pk__in=[])
    if query.number is not None:
        condition |= Q(number=query.number)
    if query.uuid is not None:
        condition |= Q(pk=query.uuid)
    hits = []
    found = services.deliveries_of(company_id).filter(condition).select_related("vehicle_card")
    for d in found[: limit * 2]:
        if _visible(actor, d):
            hits.append(Hit("trip", str(d.id), str(d.number), d.vehicle_card.name, exact=True))
    return hits[:limit]
