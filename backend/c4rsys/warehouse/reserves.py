"""Reserves of goods in open orders (ТЗ § 11.7.1, § 11.8.1, § 11.9.1).

A reserved quantity stays in the warehouse but is unavailable to other operations: issue,
consumption, move, write-off, repacking and new reserves take only what is free.
"""

from decimal import Decimal
from uuid import UUID

from django.db.models import Sum

from c4rsys.warehouse.models import SaleLine

ZERO = Decimal("0.00")


def reserved_in_form(
    lot_id: UUID,
    packaging_id: UUID | None,
    opened_package_id: UUID | None,
    exclude_line: UUID | None = None,
) -> Decimal:
    """Reserved in one form of a lot: closed packages of a packaging, loose material or an
    opened package."""
    lines = SaleLine.objects.filter(state=SaleLine.State.RESERVED, lot_id=lot_id)
    if opened_package_id is not None:
        lines = lines.filter(opened_package_id=opened_package_id)
    else:
        lines = lines.filter(opened_package__isnull=True)
        lines = (
            lines.filter(packaging__isnull=True)
            if packaging_id is None
            else lines.filter(packaging_id=packaging_id)
        )
    if exclude_line is not None:
        lines = lines.exclude(pk=exclude_line)
    total = lines.aggregate(total=Sum("quantity"))["total"]
    return total if total is not None else ZERO


def asset_reserved(asset_id: UUID) -> bool:
    return SaleLine.objects.filter(state=SaleLine.State.RESERVED, asset_id=asset_id).exists()


def vehicle_reserved(card_id: UUID) -> bool:
    return SaleLine.objects.filter(state=SaleLine.State.RESERVED, vehicle_id=card_id).exists()
