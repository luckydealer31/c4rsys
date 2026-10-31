"""Expiry of lots in the notification centre (ТЗ § 20.3.5; decision З-7: 7 days before by
default). A lot with stock warns from the company's moment before its expiry until it has no
stock left; the storekeepers of its location receive it (decision З-9)."""

from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import Sum

from c4rsys.notifications import rules
from c4rsys.notifications import services as notifications
from c4rsys.notifications.models import Notification
from c4rsys.warehouse.models import Lot, MinimumStock

EVENT = "warehouse.expiry"


def run(now: datetime) -> int:
    changed = 0
    warned: set[str] = set()
    with transaction.atomic():
        lots = (
            Lot.objects.select_related("item", "context", "context__location", "company")
            .filter(expiry_date__isnull=False, expiry_date__lte=(now + timedelta(days=90)).date())
            .annotate(left=Sum("stock__quantity"))
            .filter(left__gt=0)
        )
        for lot in lots:
            lead = rules.rule(lot.company_id, EVENT).lead_minutes
            today = now.astimezone(ZoneInfo(lot.company.timezone or "UTC")).date()
            if lot.expiry_date is None:
                continue
            if lot.expiry_date - timedelta(minutes=lead) > today:
                continue
            condition = f"{EVENT}:{lot.id}"
            warned.add(condition)
            state = "expired" if lot.expiry_date < today else "expiring"
            current = notifications.active(lot.company_id, condition)
            if current is not None and current.params.get("state") != state:
                notifications.resolve(lot.company_id, condition, "expiry_state_changed", now)
            changed += rules.raise_event(
                lot.company_id,
                EVENT,
                condition=condition,
                location_id=lot.context.location_id,
                params={
                    "item": lot.item.name,
                    "lot": lot.public_id,
                    "expiry_date": lot.expiry_date.isoformat(),
                    "state": state,
                },
                now=now,
            )
        for n in Notification.objects.filter(event=EVENT, state=Notification.State.ACTIVE):
            if n.condition not in warned:
                notifications.resolve(n.company_id, n.condition, "lot_without_stock", now)
                changed += 1
    return changed


LOW_STOCK = "warehouse.low_stock"


def low_stock(now: datetime) -> int:
    """§ 11.7.8: while the available quantity of an item in a warehouse (the stock less its
    reserves) is below the threshold set for that warehouse, the chosen employees are told; with
    no threshold nothing is raised. Nothing is purchased automatically. The notice ends when the
    quantity is back at the threshold or the threshold is removed."""
    from c4rsys.tasks import services as tasks
    from c4rsys.warehouse import prices

    changed = 0
    below: set[str] = set()
    with transaction.atomic():
        companies = MinimumStock.objects.values_list("company_id", flat=True).distinct()
        for company_id in set(companies):
            contexts = set(
                MinimumStock.objects.filter(company_id=company_id).values_list(
                    "context_id", flat=True
                )
            )
            auto = rules.rule(company_id, LOW_STOCK).auto_task
            for row in prices.low_stock(company_id, contexts):
                condition = f"{LOW_STOCK}:{row['item']['id']}:{row['context_id']}"
                below.add(condition)
                params = {
                    "item": row["item"]["name"],
                    "location": row["location_name"],
                    "available": row["available"],
                    "minimum": row["minimum"],
                }
                changed += rules.raise_event(
                    company_id,
                    LOW_STOCK,
                    condition=condition,
                    location_id=UUID(row["location_id"]),
                    context_id=UUID(str(row["context_id"])),
                    object_type="warehouse_item",
                    object_id=UUID(row["item"]["id"]),
                    params=params,
                    now=now,
                )
                # § 19.1.9 (block 16 B): the automatic task by the company's rule.
                if auto and tasks.ensure_system(
                    company_id,
                    event=LOW_STOCK,
                    condition=condition,
                    location_id=UUID(row["location_id"]),
                    context_id=UUID(str(row["context_id"])),
                    object_type="warehouse_item",
                    object_id=UUID(row["item"]["id"]),
                    params=params,
                    priority="normal",
                ):
                    changed += 1
        changed += tasks.close_stale_system(LOW_STOCK, below, done=True, result="stock_restored")
        for n in Notification.objects.filter(event=LOW_STOCK, state=Notification.State.ACTIVE):
            if n.condition not in below:
                notifications.resolve(n.company_id, n.condition, "stock_restored", now)
                changed += 1
    return changed


DISCREPANCY = "warehouse.discrepancy"


def discrepancies(now: datetime) -> int:
    """§ 20.3.5 (block 16 B): a move received with less than sent waits for the settlement of the
    shortage; the warning ends when it is settled (returned or written off)."""
    from c4rsys.warehouse.models import StockOperation

    changed = 0
    open_ones: set[str] = set()
    with transaction.atomic():
        for op in StockOperation.objects.select_related("target_context").filter(
            kind=StockOperation.Kind.MOVE, state=StockOperation.State.DISCREPANCY
        ):
            target = op.target_context or op.context
            condition = f"{DISCREPANCY}:{op.id}"
            open_ones.add(condition)
            changed += rules.raise_event(
                op.company_id,
                DISCREPANCY,
                condition=condition,
                location_id=target.location_id,
                context_id=target.id,
                object_type="warehouse_operation",
                object_id=op.id,
                params={"number": op.number},
                now=now,
            )
        for n in Notification.objects.filter(event=DISCREPANCY, state=Notification.State.ACTIVE):
            if n.condition not in open_ones:
                notifications.resolve(n.company_id, n.condition, "shortage_settled", now)
                changed += 1
    return changed


CONFIRMATION = "warehouse.adjustment_confirmation"


def confirmations(now: datetime) -> int:
    """§ 20.3.4, § 19.1.9 (block 16 B): a submitted inventory — a mandatory notification to those
    who confirm adjustments in its warehouse and a system task in the queue of its location. The
    confirmation or the rejection closes both with the result (§ 19.2.1)."""
    from c4rsys.tasks import services as tasks
    from c4rsys.warehouse.models import InventoryCount

    changed = 0
    waiting: set[str] = set()
    with transaction.atomic():
        for count in InventoryCount.objects.select_related("context").filter(
            state=InventoryCount.State.SUBMITTED
        ):
            condition = f"{CONFIRMATION}:{count.id}"
            waiting.add(condition)
            params: dict[str, object] = {"number": count.number}
            changed += rules.raise_event(
                count.company_id,
                CONFIRMATION,
                condition=condition,
                location_id=count.context.location_id,
                context_id=count.context_id,
                object_type="warehouse_inventory",
                object_id=count.id,
                params=params,
                now=now,
            )
            if tasks.ensure_system(
                count.company_id,
                event=CONFIRMATION,
                condition=condition,
                location_id=count.context.location_id,
                context_id=count.context_id,
                object_type="warehouse_inventory",
                object_id=count.id,
                params=params,
            ):
                changed += 1
        for n in Notification.objects.filter(event=CONFIRMATION, state=Notification.State.ACTIVE):
            if n.condition in waiting:
                continue
            found = InventoryCount.objects.filter(pk=str(n.object_id)).first()
            result = found.state if found is not None else "removed"
            notifications.resolve(n.company_id, n.condition, f"inventory_{result}", now)
            changed += 1
        for company_id, condition, object_id in (
            tasks.tasks_of_event(CONFIRMATION).exclude(system_condition__in=waiting)
        ).values_list("company_id", "system_condition", "object_id"):
            found = InventoryCount.objects.filter(pk=str(object_id)).first()
            result = found.state if found is not None else "removed"
            decided = result in (InventoryCount.State.CONFIRMED, InventoryCount.State.REJECTED)
            changed += tasks.close_system(
                company_id, condition, done=decided, result=f"inventory_{result}"
            )
    return changed
