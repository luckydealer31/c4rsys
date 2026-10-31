"""Block 16 part Б: an overdue service work is a working notification and, by the company's
rule, an automatic task (ТЗ § 20.3.5, § 19.1.9)."""

from datetime import timedelta

import pytest
from django.test import Client

from c4rsys.notifications import services as notifications
from c4rsys.notifications.models import Notification
from c4rsys.service.models import ServiceOperation
from c4rsys.tasks.models import Task
from tests.accounts.helpers import post
from tests.companies.helpers import key
from tests.rentals.scenario import NOW, Scenario
from tests.service.test_service import _at, _op, _work

pytestmark = pytest.mark.django_db


def _auto_task(owner: Client, s: Scenario, event: str) -> None:
    body = {
        "event": event,
        "enabled": True,
        "lead_minutes": 0,
        "repeat_minutes": 0,
        "recipient_ids": [],
        "auto_task": True,
        "expected_version": 0,
    }
    response = post(owner, f"/api/c/{s.cmp}/notification-rules/change", body, key())
    assert response.status_code == 200, response.content


def test_overdue_service_work_is_told_and_makes_a_task(owner: Client, s: Scenario) -> None:
    _auto_task(owner, s, "service.work_overdue")
    oil = _work(owner, s)
    op = _op(owner, s, [oil["id"]], planned_start=_at(1), planned_end=_at(2))
    notifications.run_checks(NOW + timedelta(hours=1))
    assert not Notification.objects.filter(event="service.work_overdue").exists()
    notifications.run_checks(NOW + timedelta(hours=3))
    n = Notification.objects.get(event="service.work_overdue")
    assert n.state == "active" and n.params["number"] == op["number"]
    assert n.context is not None and n.context.type == "service"
    task = Task.objects.get(system_event="service.work_overdue")
    assert task.state == "open"
    # Cancelled — the cause is gone: both end with the result.
    ServiceOperation.objects.filter(pk=op["id"]).update(state="cancelled")
    notifications.run_checks(NOW + timedelta(hours=3))
    n.refresh_from_db()
    task.refresh_from_db()
    assert n.state == "done" and task.state == "done"


def test_auto_task_needs_the_permission_of_the_catalogue(owner: Client, s: Scenario) -> None:
    # § 19.1.9: the company may switch on tasks for low stock and schedule conflicts too.
    _auto_task(owner, s, "warehouse.low_stock")
    _auto_task(owner, s, "rental.schedule_conflict")
    body = {
        "event": "service.works_done",
        "enabled": True,
        "lead_minutes": 0,
        "repeat_minutes": 0,
        "recipient_ids": [],
        "auto_task": True,
        "expected_version": 0,
    }
    refused = post(owner, f"/api/c/{s.cmp}/notification-rules/change", body, key())
    assert refused.status_code == 400
