"""Orders in the single search and in the list of orders: by the number or the id (ТЗ § 2.8.3).
An order is found only where it is visible (§ 2.8.2)."""

from uuid import UUID

from django.db.models import Q, QuerySet

from c4rsys.orders import services
from c4rsys.orders.models import Order
from c4rsys.platform.commands import Actor
from c4rsys.platform.search import Hit, Query


def matching(found: QuerySet[Order], query: Query) -> QuerySet[Order]:
    condition = Q(pk__in=[])
    if query.number is not None:
        condition |= Q(number=query.number)
    if query.uuid is not None:
        condition |= Q(pk=query.uuid)
    return found.filter(condition)


def provider(actor: Actor, company_id: UUID, query: Query, limit: int) -> list[Hit]:
    hits = []
    for order in matching(services.orders_of(company_id), query)[: limit * 2]:
        if services.can_view(actor, order):
            hits.append(Hit("order", str(order.id), str(order.number), order.state, exact=True))
    return hits[:limit]
