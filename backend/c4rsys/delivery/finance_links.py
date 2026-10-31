"""What the finances need to know of trips: a charged delivery belongs to its start location and
its vehicle (ТЗ § 17.3.1: a delivery to another location does not move the finances)."""

from uuid import UUID

from c4rsys.delivery.models import Delivery
from c4rsys.finance.attribution import Place


def deliveries(company_id: UUID, ids: list[UUID]) -> dict[UUID, Place]:
    return {
        d.id: Place(d.from_location_id, d.vehicle_card_id)
        for d in Delivery.objects.filter(company_id=company_id, pk__in=ids)
    }
