"""Vehicles in the single search and in the list of vehicles: by the c4rsys vehicle ID, a part of
the name, the VIN and the current registration plate (ТЗ § 2.8.3). A vehicle is found only where
the employee may view it (rights in its accounting location, § 2.8.2)."""

from uuid import UUID

from django.db.models import Q, QuerySet

from c4rsys.companies import access
from c4rsys.fleet import characteristics, services
from c4rsys.fleet.models import VehicleCard
from c4rsys.platform.commands import Actor
from c4rsys.platform.search import Hit, Query, exact_text


def _exact(query: Query) -> Q:
    text = query.text.strip()
    plate = characteristics.normalize_plate(text)
    condition = (
        Q(vehicle__public_id__iexact=text)
        | Q(vehicle__vin__iexact=text)
        | Q(vehicle__plates__number_normalized=plate, vehicle__plates__valid_to__isnull=True)
    )
    if query.uuid is not None:
        condition |= Q(pk=query.uuid)
    return condition


def matching(found: QuerySet[VehicleCard], query: Query) -> QuerySet[VehicleCard]:
    return found.filter(_exact(query) | Q(name__icontains=query.text)).distinct()


def provider(actor: Actor, company_id: UUID, query: Query, limit: int) -> list[Hit]:
    resolver = access.RightsResolver()
    found = matching(services.cards_of(company_id), query)
    exact_ids = set(found.filter(_exact(query)).values_list("id", flat=True))
    hits = []
    for card in found[: limit * 3]:
        if not resolver.has_permission(
            actor, "vehicle.view", location_id=card.accounting_location_id
        ):
            continue
        plate = card.vehicle.plates.filter(valid_to__isnull=True).first()
        detail = " · ".join(
            x
            for x in (card.vehicle.public_id, plate.number if plate else "", card.vehicle.vin)
            if x
        )
        exact = card.id in exact_ids or exact_text(card.name, query)
        hits.append(Hit("vehicle", str(card.id), card.name, detail, exact=exact))
    return hits[: limit * 2]
