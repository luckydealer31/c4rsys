"""Accruals (ТЗ § 12.2.4, § 13.6.3, § 17.2.6). Money movements are separate (T15)."""

from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db.models import Sum
from django.utils import timezone

from c4rsys.finance.models import ChargeEntry
from c4rsys.platform.commands import CommandContext


def current_charge(source_type: str, source_id: UUID) -> Decimal:
    total = ChargeEntry.objects.filter(source_type=source_type, source_id=source_id).aggregate(
        total=Sum("amount")
    )["total"]
    return total if total is not None else Decimal(0)


def set_charge(
    ctx: CommandContext,
    *,
    company_id: UUID,
    order_id: UUID,
    client_id: UUID,
    source_type: str,
    source_id: UUID,
    amount: Decimal,
    currency: str,
    reason: str,
    calculation: dict[str, Any],
) -> ChargeEntry | None:
    """Brings the charge of a source to `amount` by appending the difference. Payments already
    made are not touched: the difference becomes debt or overpayment (§ 13.6.3)."""
    delta = amount - current_charge(source_type, source_id)
    if delta == 0:
        return None
    entry = ChargeEntry.objects.create(
        company_id=company_id,
        order_id=order_id,
        client_id=client_id,
        source_type=source_type,
        source_id=source_id,
        amount=delta,
        currency_id=currency,
        reason=reason,
        calculation=calculation,
        created_by_user_id=ctx.actor.user_id,
        created_at=timezone.now(),
    )
    ctx.audit(
        "charge.recorded",
        object_type="charge_entry",
        object_id=entry.id,
        after={
            "order": str(order_id),
            "source_type": source_type,
            "source_id": str(source_id),
            "delta": delta,
            "total": amount,
            "currency": currency,
            "reason": reason,
        },
    )
    return entry


def charged_for_order(company_id: UUID, order_id: UUID) -> dict[str, Decimal]:
    """Provider for orders: charged amounts per currency."""
    rows = (
        ChargeEntry.objects.filter(company_id=company_id, order_id=order_id)
        .values("currency_id")
        .annotate(total=Sum("amount"))
    )
    return {r["currency_id"]: r["total"] for r in rows}


def charge_ids_for_source(source_type: str, source_id: UUID) -> list[UUID]:
    return list(
        ChargeEntry.objects.filter(source_type=source_type, source_id=source_id).values_list(
            "id", flat=True
        )
    )
