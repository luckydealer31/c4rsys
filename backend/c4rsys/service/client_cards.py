"""The vehicles of service operations of a client's orders, shown in the client card (ТЗ § 8.1.2,
§ 6.1.14). A service operation keeps no client of its own: it follows its order at a split."""

from uuid import UUID

from c4rsys.service.models import ServiceOperation


def order_vehicles(company_id: UUID, order_ids: list[UUID]) -> list[tuple[UUID, UUID, str]]:
    return [
        (order_id, card_id, "service")
        for order_id, card_id in ServiceOperation.objects.filter(
            company_id=company_id, order_id__in=order_ids
        ).values_list("order_id", "vehicle_card_id")
    ]
