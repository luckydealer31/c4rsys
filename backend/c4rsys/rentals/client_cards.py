"""Rentals and the client cards (ТЗ § 6.5.3, § 6.1.14, § 8.1.2): the rentals of an order follow
it to the other card at the split of a merge; the vehicles of the client's orders are shown in
the client card."""

from uuid import UUID

from c4rsys.platform.commands import CommandContext
from c4rsys.rentals.models import Rental


def on_client_moved(
    ctx: CommandContext, company_id: UUID, order_ids: list[UUID], source: UUID, target: UUID
) -> None:
    moved = list(
        Rental.objects.filter(
            company_id=company_id, order_id__in=order_ids, client_id=source
        ).values_list("id", flat=True)
    )
    Rental.objects.filter(pk__in=moved).update(client_id=target)
    for rental_id in moved:
        ctx.audit(
            "rental.client_moved",
            object_type="rental",
            object_id=rental_id,
            before={"client": str(source)},
            after={"client": str(target), "reason": "card_split"},
        )


def order_vehicles(company_id: UUID, order_ids: list[UUID]) -> list[tuple[UUID, UUID, str]]:
    return [
        (order_id, card_id, "rental")
        for order_id, card_id in Rental.objects.filter(
            company_id=company_id, order_id__in=order_ids
        ).values_list("order_id", "vehicle_card_id")
    ]
