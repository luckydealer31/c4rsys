"""Service operations in the single search: by the number or the id (ТЗ § 2.8.2). An operation
is found only with «View service» in its location."""

from uuid import UUID

from django.db.models import Q

from c4rsys.companies import access
from c4rsys.platform.commands import Actor
from c4rsys.platform.search import Hit, Query
from c4rsys.service import operations


def provider(actor: Actor, company_id: UUID, query: Query, limit: int) -> list[Hit]:
    condition = Q(pk__in=[])
    if query.number is not None:
        condition |= Q(number=query.number)
    if query.uuid is not None:
        condition |= Q(pk=query.uuid)
    resolver = access.RightsResolver()
    hits = []
    found = operations.operations_of(company_id).filter(condition).select_related("vehicle_card")
    for op in found[: limit * 2]:
        if not resolver.has_permission(actor, "service.view", location_id=op.location_id):
            continue
        hits.append(
            Hit("service_operation", str(op.id), str(op.number), op.vehicle_card.name, True)
        )
    return hits[:limit]
