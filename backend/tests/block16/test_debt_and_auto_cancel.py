"""Payment term and overdue debt (ТЗ § 12.2.6) and the automatic cancellation of a booking
(§ 14.8.1)."""

from datetime import datetime, timedelta
from typing import Any
from unittest import mock

import pytest
from django.test import Client
from django.utils import timezone

from c4rsys.finance.models import ChargeEntry
from c4rsys.notifications import services as notifications
from c4rsys.notifications.models import Notification
from c4rsys.orders.models import OrderApproval
from c4rsys.platform.commands import SYSTEM_USER_ID
from c4rsys.platform.models import AuditEvent
from c4rsys.rentals import bookings
from c4rsys.rentals.models import Rental
from c4rsys.tasks.models import Task
from tests.accounts.helpers import post
from tests.companies.helpers import hire, key
from tests.rentals.scenario import Scenario, build
from tests.rentals.scenario import issue as scenario_issue

pytestmark = pytest.mark.django_db


@pytest.fixture
def s(owner: Client) -> Scenario:
    return build(owner)


def _order_with_debt(owner: Client, s: Scenario) -> dict[str, Any]:
    started = (timezone.now() - timedelta(days=2)).isoformat()
    ends = (timezone.now() + timedelta(days=1)).isoformat()
    rental = scenario_issue(owner, s, actual_start=started, planned_end=ends).json()["rental"]
    order: dict[str, Any] = owner.get(f"/api/c/{s.cmp}/orders/{rental['order_id']}").json()["order"]
    return order


def _due(client: Client, s: Scenario, order: dict[str, Any], due_on: str | None) -> Any:
    body = {"due_on": due_on, "expected_version": order["version"], "reason": "agreed"}
    return post(client, f"/api/c/{s.cmp}/orders/{order['id']}/payment-due", body, key())


def test_payment_term_marks_and_filters_overdue_debt(owner: Client, s: Scenario) -> None:
    order = _order_with_debt(owner, s)
    assert order["payment_due_on"] is None and order["overdue"] is False
    # Without a term the debt is never overdue (PROPOSAL).
    listed = owner.get(f"/api/c/{s.cmp}/orders", {"debt": "overdue"}).json()["orders"]
    assert listed == []
    assert [o["id"] for o in owner.get(f"/api/c/{s.cmp}/orders", {"debt": "any"}).json()["orders"]]

    yesterday = (timezone.localdate() - timedelta(days=2)).isoformat()
    changed = _due(owner, s, order, yesterday)
    assert changed.status_code == 200, changed.content
    order = changed.json()["order"]
    assert order["payment_due_on"] == yesterday and order["overdue"] is True
    listed = owner.get(f"/api/c/{s.cmp}/orders", {"debt": "overdue"}).json()["orders"]
    assert [o["id"] for o in listed] == [order["id"]]
    entry = AuditEvent.objects.get(action="order.payment_due_changed")
    assert entry.after == {"payment_due_on": yesterday} and entry.reason == "agreed"

    # The overdue debt is a working notification to the responsible of the order.
    assert notifications.run_checks(timezone.now()) >= 1
    n = Notification.objects.get(event="finance.payment_overdue")
    assert n.state == Notification.State.ACTIVE and n.params["number"] == order["number"]
    # A new term ends the overdue state and the notification.
    later = (timezone.localdate() + timedelta(days=10)).isoformat()
    order = _due(owner, s, order, later).json()["order"]
    assert order["overdue"] is False
    notifications.run_checks(timezone.now())
    n.refresh_from_db()
    assert n.state != Notification.State.ACTIVE
    # Repeating the same term changes nothing.
    assert _due(owner, s, order, later).status_code == 422
    assert owner.get(f"/api/c/{s.cmp}/orders", {"debt": "bad"}).status_code == 400


def test_payment_term_needs_the_right_to_change_orders(owner: Client, s: Scenario) -> None:
    order = _order_with_debt(owner, s)
    viewer, _ = hire(owner, s.cmp, "v@example.com", [{"type": "grant", "permission": "order.view"}])
    assert _due(viewer, s, order, timezone.localdate().isoformat()).status_code == 403


def _at(days: float) -> datetime:
    return timezone.now().replace(microsecond=0, second=0) + timedelta(days=days)


def _book(owner: Client, s: Scenario, **data: Any) -> dict[str, Any]:
    body = {
        "vehicle_card_id": s.vehicle,
        "planned_start": _at(1).isoformat(),
        "planned_end": _at(2).isoformat(),
        "issue_location_id": s.location,
        "client_id": s.client,
        "tariff_id": s.tariff,
        **data,
    }
    response = post(owner, f"/api/c/{s.cmp}/bookings", body, key())
    assert response.status_code == 201, response.content
    rental: dict[str, Any] = response.json()["rental"]
    return rental


def _enable(owner: Client, s: Scenario) -> None:
    put = owner.put(
        f"/api/c/{s.cmp}/rental-policy?location_id={s.location}",
        data={"enabled": True, "after_minutes": 30},
        content_type="application/json",
        headers=key(),
    )
    assert put.status_code == 200, put.content


def test_auto_cancel_is_a_journaled_system_command(owner: Client, s: Scenario) -> None:
    _enable(owner, s)
    rental = _book(owner, s)
    charged = sum(c.amount for c in ChargeEntry.objects.filter(source_id=rental["id"]))
    assert charged > 0
    # A pending approval of the position (§ 12.3) is withdrawn as at a manual cancellation.
    OrderApproval.objects.filter(item_id=rental["id"]).update(state="pending")
    assert bookings.cancel_no_shows(_at(1) + timedelta(minutes=31)) == 1
    stored = Rental.objects.get(pk=rental["id"])
    assert (stored.state, stored.cancel_reason) == ("cancelled", "no_show")
    entry = AuditEvent.objects.get(action="rental.booking_cancelled", object_id=rental["id"])
    assert entry.actor_user_id is None and entry.system_process == "booking_auto_cancel"
    assert entry.after is not None and entry.after["automatic"] is True
    # The charge of the cancelled booking is brought to zero; the system entry names no user.
    assert sum(c.amount for c in ChargeEntry.objects.filter(source_id=rental["id"])) == 0
    last = ChargeEntry.objects.filter(source_id=rental["id"]).order_by("-created_at").first()
    assert last is not None and last.created_by_user_id == SYSTEM_USER_ID
    assert not OrderApproval.objects.filter(item_id=rental["id"], state="pending").exists()


def test_delivery_on_the_way_keeps_the_booking_with_a_conflict(owner: Client, s: Scenario) -> None:
    _enable(owner, s)
    rental = _book(owner, s)
    trip = [{"kind": "delivery", "id": "x", "number": 7}]
    with mock.patch("c4rsys.delivery.services.issue_preparation", return_value=trip):
        assert bookings.cancel_no_shows(_at(1) + timedelta(minutes=31)) == 0
    stored = Rental.objects.get(pk=rental["id"])
    # § 14.8.1: not cancelled; the automatic cancellation is taken off and an employee decides.
    assert stored.state == "booked" and stored.auto_cancel_at is None
    held = AuditEvent.objects.get(action="rental.auto_cancel_held")
    assert held.system_process == "booking_auto_cancel"
    n = Notification.objects.get(event="rental.auto_cancel_held")
    assert n.params["trips"] == [7]
    task = Task.objects.get(system_event="rental.auto_cancel_held")
    assert task.object_id == stored.id
    # Nothing more fires for this booking.
    assert bookings.cancel_no_shows(_at(1) + timedelta(hours=5)) == 0
    # The employee cancels by hand: the conflict and the task end.
    cancel = post(
        owner,
        f"/api/c/{s.cmp}/bookings/{rental['id']}/cancel",
        {"expected_version": stored.version, "reason": "client did not come"},
        key(),
    )
    assert cancel.status_code == 200, cancel.content
    notifications.run_checks(timezone.now())
    n.refresh_from_db()
    task.refresh_from_db()
    assert n.state != Notification.State.ACTIVE and task.state not in ("open", "in_progress")


def test_new_booking_refers_to_the_cancelled_one(owner: Client, s: Scenario) -> None:
    first = _book(owner, s)
    refused = post(
        owner,
        f"/api/c/{s.cmp}/bookings",
        {
            "vehicle_card_id": s.vehicle,
            "planned_start": _at(5).isoformat(),
            "planned_end": _at(6).isoformat(),
            "previous_booking_id": first["id"],
        },
        key(),
    )
    # Only a cancelled booking can be the previous one; it is not restored.
    assert refused.json()["error"]["code"] == "PREVIOUS_BOOKING_NOT_CANCELLED"
    cancel = post(
        owner,
        f"/api/c/{s.cmp}/bookings/{first['id']}/cancel",
        {"expected_version": first["version"], "reason": "no show"},
        key(),
    )
    assert cancel.status_code == 200, cancel.content
    again = _book(owner, s, previous_booking_id=first["id"])
    assert again["booking"]["previous_booking"] == {
        "id": first["id"],
        "number": first["booking"]["number"],
    }
    assert Rental.objects.get(pk=first["id"]).state == "cancelled"


def test_past_due_orders_are_read_in_batches(owner: Client, s: Scenario) -> None:
    # Audit item 13: settled orders past their term are read every run; their amounts come in a
    # fixed number of queries, however many there are.
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from c4rsys.orders import notices
    from c4rsys.orders.models import Order

    order = Order.objects.get(pk=_order_with_debt(owner, s)["id"])
    Order.objects.filter(pk=order.pk).update(
        payment_due_on=timezone.localdate() - timedelta(days=2)
    )

    def queries() -> int:
        with CaptureQueriesContext(connection) as captured:
            notices._overdue(timezone.now())
        return len(captured)

    queries()  # the warning of the order with debt is raised once
    few = queries()
    last = Order.objects.order_by("-number").first()
    assert last is not None
    for number in range(last.number + 1, last.number + 31):
        # Orders without operations: past their term, nothing to pay.
        Order.objects.create(
            company_id=order.company_id,
            number=number,
            client_id=order.client_id,
            created_by_user_id=order.created_by_user_id,
            payment_due_on=timezone.localdate() - timedelta(days=5),
        )
    assert queries() == few
    assert Notification.objects.filter(event="finance.payment_overdue").count() == 1
