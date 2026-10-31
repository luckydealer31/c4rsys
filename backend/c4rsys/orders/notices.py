"""Debt of a closed order in the notification centre (ТЗ § 20.3.5, § 19.1.9; decisions З-7,
З-8, З-9): a warning when an order is closed with debt, without repetitions, until the debt is
paid; with the company's rule also an automatic task (off by default). The responsible of the
order receives it, else those who see the finances."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from django.db import transaction

from c4rsys.notifications import rules
from c4rsys.notifications import services as notifications
from c4rsys.notifications.models import Notification
from c4rsys.orders import services as orders
from c4rsys.orders.models import Order
from c4rsys.platform.models import AuditEvent

EVENT = "finance.debt"


def _debt(order: Order) -> dict[str, str]:
    return {
        c: str(m.balance) for c, m in orders.amounts_of(order).items() if m.balance > Decimal(0)
    }


def on_order_closed(entry: AuditEvent) -> int:
    from c4rsys.tasks import services as tasks

    order = Order.objects.filter(pk=entry.object_id).first()
    if order is None or order.client_id is None:
        return 0
    debt = _debt(order)
    if not debt:
        return 0
    condition = f"{EVENT}:{order.id}"
    params = {"number": order.number, "debt": debt}
    raised = rules.raise_event(
        order.company_id,
        EVENT,
        condition=condition,
        object_type="order",
        object_id=order.id,
        params=params,
        responsible=[order.responsible_employment_id],
    )
    if rules.rule(order.company_id, EVENT).auto_task:
        tasks.ensure_system(
            order.company_id,
            event=EVENT,
            condition=condition,
            location_id=None,
            object_type="order",
            object_id=order.id,
            params=params,
            priority="normal",
        )
    return int(raised)


def _orders_of_correction(entry: AuditEvent) -> list[UUID]:
    """The orders whose payment the corrected record touched."""
    if entry.object_type == "finance_offset":
        from c4rsys.finance.models import Offset

        found = Offset.objects.filter(pk=entry.object_id).values_list("order_id", flat=True)
        return [x for x in found if x is not None]
    if entry.action == "payment.relinked" and entry.before:
        value = entry.before.get("order")
        return [UUID(value)] if value else []
    return []


def on_payment_corrected(entry: AuditEvent) -> int:
    """§ 17.2.16 (block 16): a cancelled or corrected set-off, or a payment moved off a closed
    order, makes the debt appear again: the employees who control debts by the company's rule
    are told with the sum, the reason and the author. Nothing goes to the client."""
    from c4rsys.accounts import services as accounts

    told = 0
    for order_id in _orders_of_correction(entry):
        order = Order.objects.filter(pk=order_id).first()
        if order is None or order.state != Order.State.CLOSED or order.client_id is None:
            continue
        debt = _debt(order)
        if not debt:
            continue
        after = entry.after or {}
        author = (
            accounts.names({entry.actor_user_id}).get(entry.actor_user_id, "")
            if entry.actor_user_id
            else ""
        )
        told += int(
            rules.raise_event(
                order.company_id,
                EVENT,
                condition=f"{EVENT}:{order.id}:{entry.id}",
                object_type="order",
                object_id=order.id,
                params={
                    "number": order.number,
                    "debt": debt,
                    "corrected": True,
                    "reason": entry.reason or after.get("reason") or after.get("comment") or "",
                    "author": author,
                    "record": entry.object_id,
                    "record_type": entry.object_type,
                },
            )
        )
    return told


OVERDUE = "finance.payment_overdue"


def _overdue(now: datetime) -> int:
    """§ 12.2.6: an order whose debt is unpaid after its term of payment; the warning repeats by
    the company's rule and ends when the debt is paid or the term is moved."""
    from c4rsys.tasks import services as tasks

    changed = 0
    active: set[str] = set()
    for order in Order.objects.filter(payment_due_on__isnull=False).exclude(
        state=Order.State.CANCELLED
    ):
        today = orders.today_of(order.company_id)
        if not orders.is_overdue(order, today):
            continue
        condition = f"{OVERDUE}:{order.id}"
        active.add(condition)
        params = {
            "number": order.number,
            "debt": _debt(order),
            "due_on": order.payment_due_on.isoformat() if order.payment_due_on else None,
        }
        changed += rules.raise_event(
            order.company_id,
            OVERDUE,
            condition=condition,
            object_type="order",
            object_id=order.id,
            params=params,
            responsible=[order.responsible_employment_id],
            now=now,
        )
        if rules.rule(order.company_id, OVERDUE).auto_task:
            tasks.ensure_system(
                order.company_id,
                event=OVERDUE,
                condition=condition,
                location_id=None,
                object_type="order",
                object_id=order.id,
                params=params,
                priority="normal",
            )
    for n in Notification.objects.filter(event=OVERDUE, state=Notification.State.ACTIVE):
        if n.condition in active:
            continue
        notifications.resolve(n.company_id, n.condition, "overdue_settled", now)
        tasks.close_system(n.company_id, n.condition, done=True, result="overdue_settled")
        changed += 1
    return changed


def run(now: datetime) -> int:
    """The debt is paid: the warning and the task end."""
    from c4rsys.tasks import services as tasks

    changed = 0
    with transaction.atomic():
        changed += _overdue(now)
        for n in Notification.objects.filter(event=EVENT, state=Notification.State.ACTIVE):
            order = Order.objects.filter(pk=str(n.object_id)).first()
            if order is None or not _debt(order):
                notifications.resolve(n.company_id, n.condition, "debt_paid", now)
                tasks.close_system(n.company_id, n.condition, done=True, result="debt_paid")
                changed += 1
    return changed
