"""Rentals and bookings made on a main vehicle card after a merge, for the split of the merge
(ТЗ § 8.6.5): listed, and moved to the card the employee assigns them to."""

from datetime import datetime
from typing import Any
from uuid import UUID

from c4rsys.fleet.merges import CardRecords
from c4rsys.fleet.models import VehicleCard
from c4rsys.platform.commands import CommandContext
from c4rsys.rentals.models import Rental


def since(company_id: UUID, card_id: UUID, moment: datetime) -> list[dict[str, Any]]:
    return [
        {
            "kind": "rental",
            "id": str(r.id),
            "label": r.state,
            "at": (r.actual_start or r.planned_start or r.created_at).isoformat(),
        }
        for r in Rental.objects.filter(
            company_id=company_id, vehicle_card_id=card_id, created_at__gte=moment
        ).order_by("created_at")
    ]


def reassign(
    ctx: CommandContext, company_id: UUID, ids: list[UUID], source: VehicleCard, target: VehicleCard
) -> list[UUID]:
    for rental in Rental.objects.select_for_update().filter(
        company_id=company_id, vehicle_card=source, pk__in=ids
    ):
        rental.vehicle_card = target
        rental.save(update_fields=["vehicle_card"])
        ctx.audit(
            "rental.vehicle_card_assigned",
            object_type="rental",
            object_id=rental.id,
            before={"vehicle_card": str(source.id)},
            after={"vehicle_card": str(target.id)},
            reason="vehicle split",
        )
    return []


PROVIDER = CardRecords("rental", since, reassign)
