"""Inspections (with their damages) made on a main vehicle card after a merge, for the split of
the merge (ТЗ § 8.6.5). An inspection of a rental or a service operation follows it."""

from datetime import datetime
from typing import Any
from uuid import UUID

from c4rsys.fleet.merges import CardRecords
from c4rsys.fleet.models import VehicleCard
from c4rsys.inspections.models import Damage, Inspection
from c4rsys.platform.commands import CommandContext


def since(company_id: UUID, card_id: UUID, moment: datetime) -> list[dict[str, Any]]:
    result = []
    for i in Inspection.objects.filter(
        company_id=company_id, vehicle_card_id=card_id, entered_at__gte=moment
    ).order_by("entered_at"):
        parent = (
            f"rental:{i.rental_id}"
            if i.rental_id
            else f"service:{i.service_operation_id}"
            if i.service_operation_id
            else None
        )
        result.append(
            {
                "kind": "inspection",
                "id": str(i.id),
                "label": i.kind,
                "at": i.performed_at.isoformat(),
                "parent": parent,
            }
        )
    return result


def reassign(
    ctx: CommandContext, company_id: UUID, ids: list[UUID], source: VehicleCard, target: VehicleCard
) -> list[UUID]:
    for i in Inspection.objects.select_for_update().filter(
        company_id=company_id, vehicle_card=source, pk__in=ids
    ):
        i.vehicle_card = target
        i.save(update_fields=["vehicle_card"])
        ctx.audit(
            "inspection.vehicle_card_assigned",
            object_type="inspection",
            object_id=i.id,
            before={"vehicle_card": str(source.id)},
            after={"vehicle_card": str(target.id)},
            reason="vehicle split",
        )
    Damage.objects.filter(company_id=company_id, inspection_id__in=ids).update(
        vehicle_card=target, vehicle_id=target.vehicle_id
    )
    return []


PROVIDER = CardRecords("inspection", since, reassign)
