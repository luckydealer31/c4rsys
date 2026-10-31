"""Deliveries and moves made on a main vehicle card after a merge, for the split of the merge
(ТЗ § 8.6.5)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from c4rsys.delivery.models import Delivery
from c4rsys.fleet.merges import CardRecords
from c4rsys.fleet.models import VehicleCard
from c4rsys.platform.commands import CommandContext


def since(company_id: UUID, card_id: UUID, moment: datetime) -> list[dict[str, Any]]:
    return [
        {
            "kind": "delivery",
            "id": str(d.id),
            "label": f"№ {d.number}",
            "at": d.created_at.isoformat(),
        }
        for d in Delivery.objects.filter(
            company_id=company_id, vehicle_card_id=card_id, created_at__gte=moment
        ).order_by("created_at")
    ]


def reassign(
    ctx: CommandContext, company_id: UUID, ids: list[UUID], source: VehicleCard, target: VehicleCard
) -> list[UUID]:
    for d in Delivery.objects.select_for_update().filter(
        company_id=company_id, vehicle_card=source, pk__in=ids
    ):
        d.vehicle_card = target
        d.save(update_fields=["vehicle_card"])
        ctx.audit(
            "delivery.vehicle_card_assigned",
            object_type="delivery",
            object_id=d.id,
            before={"vehicle_card": str(source.id)},
            after={"vehicle_card": str(target.id)},
            reason="vehicle split",
        )
    return []


PROVIDER = CardRecords("delivery", since, reassign)
