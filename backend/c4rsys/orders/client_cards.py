"""Orders in the merge and the split of client cards (ТЗ § 6.5.2–6.5.3).

PROPOSAL: a duplicate with an open or preliminary order is not merged — its operations would go
on under a closed card. The orders made after a merge are the records assigned at its split; an
order takes its operations, payments and documents with it.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from c4rsys.orders import services
from c4rsys.orders.models import Order
from c4rsys.parties import merges
from c4rsys.platform.commands import CommandContext


def merge_check(company_id: UUID, duplicate_id: UUID) -> list[str]:
    open_orders = Order.objects.filter(
        company_id=company_id,
        client_id=duplicate_id,
        state__in=(Order.State.OPEN, Order.State.PRELIMINARY),
    )
    return ["duplicate_has_open_orders"] if open_orders.exists() else []


def since(company_id: UUID, client_id: UUID, moment: datetime) -> list[dict[str, Any]]:
    return [
        {"kind": "order", "id": str(o.id), "label": str(o.number), "at": o.created_at.isoformat()}
        for o in Order.objects.filter(
            company_id=company_id, client_id=client_id, created_at__gte=moment
        ).order_by("created_at")
    ]


def reassign(
    ctx: CommandContext, company_id: UUID, ids: list[UUID], source: UUID, target: UUID
) -> None:
    services.move_to_client(ctx, company_id, ids, source, target)


PROVIDER = merges.CardRecords("client", "order", since, reassign)
