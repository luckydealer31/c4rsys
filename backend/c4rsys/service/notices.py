"""Notifications of the service (ТЗ § 20.3.5, § 19.1.9; decisions З-7, З-8, З-9).

* Maintenance soon or required by a rule of the vehicle: a warning while it lasts; with the
  company's rule — also an automatic task (off by default).
* Works done of a service operation: information to the responsible of its order, else to those
  who see the service in the location.
"""

from datetime import datetime
from typing import Any

from django.db import transaction

from c4rsys.notifications import rules
from c4rsys.notifications import services as notifications
from c4rsys.notifications.models import Notification
from c4rsys.platform.models import AuditEvent
from c4rsys.service import maintenance
from c4rsys.service.models import MaintenanceRule, ServiceOperation

EVENT = "service.maintenance_due"


def run(now: datetime) -> int:
    from c4rsys.tasks import services as tasks

    changed = 0
    due: set[str] = set()
    with transaction.atomic():
        companies = set(
            MaintenanceRule.objects.filter(state=MaintenanceRule.State.ACTIVE).values_list(
                "company_id", flat=True
            )
        )
        for company_id in companies:
            auto = rules.rule(company_id, EVENT).auto_task
            for card, rule, status in maintenance.due(company_id):
                condition = f"{EVENT}:{rule.id}"
                due.add(condition)
                params: dict[str, Any] = {
                    "vehicle": card.name,
                    "rule": rule.name,
                    "state": status.state,
                    "due_on": status.due_on.isoformat() if status.due_on else None,
                    "due_odometer": str(status.due_odometer) if status.due_odometer else None,
                }
                current = notifications.active(company_id, condition)
                if current is not None and current.params.get("state") != status.state:
                    # «Soon» became «required»: a new step of the same warning.
                    notifications.resolve(company_id, condition, "maintenance_state_changed", now)
                changed += rules.raise_event(
                    company_id,
                    EVENT,
                    condition=condition,
                    location_id=card.accounting_location_id,
                    object_type="vehicle",
                    object_id=card.id,
                    params=params,
                    now=now,
                )
                if auto and tasks.ensure_system(
                    company_id,
                    event=EVENT,
                    condition=condition,
                    location_id=card.accounting_location_id,
                    object_type="vehicle",
                    object_id=card.id,
                    params=params,
                    priority="normal",
                ):
                    changed += 1
        for n in Notification.objects.filter(event=EVENT, state=Notification.State.ACTIVE):
            if n.condition not in due:
                notifications.resolve(n.company_id, n.condition, "maintenance_done", now)
                tasks.close_system(n.company_id, n.condition, done=True, result="maintenance_done")
                changed += 1
    return changed


def on_works_done(entry: AuditEvent) -> int:
    from c4rsys.orders import services as orders

    op = ServiceOperation.objects.select_related("vehicle_card").filter(pk=entry.object_id).first()
    if op is None:
        return 0
    order = orders.get_order(op.company_id, op.order_id) if op.order_id else None
    return int(
        rules.raise_event(
            op.company_id,
            "service.works_done",
            condition=f"service.works_done:{op.id}",
            location_id=op.location_id,
            object_type="service_operation",
            object_id=op.id,
            params={
                "number": op.number,
                "vehicle": op.vehicle_card.name if op.vehicle_card_id else None,
            },
            responsible=[order.responsible_employment_id] if order else [],
            done=True,
        )
    )
