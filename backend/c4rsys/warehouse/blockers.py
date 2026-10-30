"""Unfinished warehouse operations that keep a company from deactivation (ТЗ § 3.6.1).

PROPOSAL of the composition (TODO(spec)): a move sent and not received or received with a
shortage awaiting a decision, a receipt not posted, an inventory not confirmed or rejected, and an
import checked but neither posted nor cancelled.
"""

from uuid import UUID

from c4rsys.warehouse.models import ImportOperation, InventoryCount, Receipt, StockOperation


def company_blockers(company_id: UUID) -> list[str]:
    found = []
    if StockOperation.objects.filter(
        company_id=company_id,
        state__in=(StockOperation.State.IN_TRANSIT, StockOperation.State.DISCREPANCY),
    ).exists():
        found.append("warehouse_moves_not_finished")
    if Receipt.objects.filter(company_id=company_id, state=Receipt.State.DRAFT).exists():
        found.append("warehouse_receipts_not_posted")
    if InventoryCount.objects.filter(
        company_id=company_id,
        state__in=(InventoryCount.State.DRAFT, InventoryCount.State.SUBMITTED),
    ).exists():
        found.append("warehouse_inventories_not_finished")
    if ImportOperation.objects.filter(
        company_id=company_id, state=ImportOperation.State.CHECKED
    ).exists():
        found.append("warehouse_imports_not_finished")
    return found
