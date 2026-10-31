"""The financial categories passed to the owner of a managed vehicle (ТЗ § 8.3.7, § 8.3.15):
the income charged by his vehicle and the expenses of his vehicle, each switched on apart. Only
this vehicle: no clients, no other vehicles, no general accounting. The settlements between the
company and the owner are not kept in the system yet (TODO(spec): § 8.3.7 «начислено ему,
выплачено, осталось выплатить» has no source)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from c4rsys.companies.models import Company


def income(company_id: UUID, card_id: UUID, until: datetime | None) -> list[dict[str, Any]]:
    """Charges of the operations of the vehicle (rentals, mileage, manual charges, service works
    and materials, deliveries, damages): the amount, the currency, the moment and the kind."""
    from c4rsys.finance.models import ChargeEntry
    from c4rsys.reports.rental import _vehicle_of_charges

    company = Company.objects.get(pk=company_id)
    owner = _vehicle_of_charges(company)
    query = ChargeEntry.objects.filter(company_id=company_id)
    if until is not None:
        query = query.filter(created_at__lte=until)
    result = []
    for c in query.order_by("-created_at"):
        kind = c.source_type.removeprefix("discount_")
        if owner.get((kind, c.source_id)) != card_id:
            continue
        result.append(
            {
                "at": c.created_at,
                "kind": c.source_type,
                "amount": c.amount,
                "currency": c.currency_id,
            }
        )
    return result


def expenses(company_id: UUID, card_id: UUID, until: datetime | None) -> list[dict[str, Any]]:
    """Posted payments of the company linked to the vehicle and the active shares of expenses
    attributed to it (§ 17.3.3)."""
    from c4rsys.finance.models import ExpenseShare, MoneyMovement

    result: list[dict[str, Any]] = []
    movements = MoneyMovement.objects.filter(
        company_id=company_id,
        vehicle_id=card_id,
        state=MoneyMovement.State.POSTED,
        direction="out",
    ).exclude(kind__in=("transfer", "opening", "reversal", "refund", "deposit"))
    shares = ExpenseShare.objects.select_related("money_movement").filter(
        company_id=company_id, vehicle_id=card_id, state=ExpenseShare.State.ACTIVE
    )
    if until is not None:
        movements = movements.filter(created_at__lte=until)
        shares = shares.filter(created_at__lte=until)
    shared = set()
    for s in shares:
        shared.add(s.money_movement_id)
        result.append(
            {
                "at": s.created_at,
                "kind": "expense_share",
                "amount": s.amount,
                "currency": s.money_movement.currency_id,
            }
        )
    for m in movements:
        if m.id in shared:
            continue
        result.append(
            {"at": m.created_at, "kind": m.kind, "amount": m.amount, "currency": m.currency_id}
        )
    return result
