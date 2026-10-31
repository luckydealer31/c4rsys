"""Service operations made on a main vehicle card after a merge, for the split of the merge
(ТЗ § 8.6.5): listed, and moved to the card the employee assigns them to with the readings of
their jobs."""

from datetime import datetime
from typing import Any
from uuid import UUID

from c4rsys.fleet.merges import CardRecords
from c4rsys.fleet.models import VehicleCard
from c4rsys.platform.commands import CommandContext
from c4rsys.service.models import ServiceJob, ServiceOperation


def since(company_id: UUID, card_id: UUID, moment: datetime) -> list[dict[str, Any]]:
    return [
        {
            "kind": "service",
            "id": str(op.id),
            "label": f"№ {op.number}",
            "at": op.created_at.isoformat(),
        }
        for op in ServiceOperation.objects.filter(
            company_id=company_id, vehicle_card_id=card_id, created_at__gte=moment
        ).order_by("created_at")
    ]


def reassign(
    ctx: CommandContext, company_id: UUID, ids: list[UUID], source: VehicleCard, target: VehicleCard
) -> list[UUID]:
    for op in ServiceOperation.objects.select_for_update().filter(
        company_id=company_id, vehicle_card=source, pk__in=ids
    ):
        op.vehicle_card = target
        op.save(update_fields=["vehicle_card"])
        ctx.audit(
            "service_operation.vehicle_card_assigned",
            object_type="service_operation",
            object_id=op.id,
            before={"vehicle_card": str(source.id)},
            after={"vehicle_card": str(target.id)},
            reason="vehicle split",
        )
    # The readings of the jobs move with the operation.
    return list(ServiceJob.objects.filter(operation_id__in=ids).values_list("id", flat=True))


PROVIDER = CardRecords("service", since, reassign)
