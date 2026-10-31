"""Block 16: notifications of rentals to the responsible (ТЗ § 12.1.10, § 20.2.2), a missing
deposit (§ 14.3.3), a late return touching the next booking (§ 14.6.2), the settlement task of a
rejected exception (§ 14.3.7) and the correction of a receipt with a deposit part (§ 14.7.3)."""

from datetime import timedelta
from typing import Any

import pytest
from django.test import Client
from django.utils import timezone

from c4rsys.finance.models import DepositEntry
from c4rsys.notifications import services as notifications
from c4rsys.notifications.models import Notification, NotificationRecipient
from c4rsys.rentals.models import IssueException, Rental
from c4rsys.tasks.models import Task
from tests.accounts.helpers import post
from tests.companies.helpers import hire, key
from tests.finance.test_deposits import _account, _balance, _deposit, _receive, _with_deposit
from tests.rentals.scenario import Scenario, build
from tests.rentals.scenario import issue as scenario_issue

pytestmark = pytest.mark.django_db


@pytest.fixture
def s(owner: Client) -> Scenario:
    return build(owner)


def _issue(client: Client, s: Scenario, **extra: Any) -> dict[str, Any]:
    started = (timezone.now() - timedelta(hours=2)).isoformat()
    response = scenario_issue(client, s, actual_start=started, **extra)
    assert response.status_code == 201, response.content
    rental: dict[str, Any] = response.json()["rental"]
    return rental


def _recipients(event: str) -> set[str]:
    n = Notification.objects.get(event=event)
    return {
        str(x)
        for x in NotificationRecipient.objects.filter(notification=n).values_list(
            "employment_id", flat=True
        )
    }


def test_deposit_shortfall_is_told_and_the_receipt_is_corrected_with_its_part(
    owner: Client, s: Scenario
) -> None:
    _with_deposit(owner, s)
    rental = _issue(owner, s)
    account = _account(owner, s)
    paid = _receive(
        owner, s, account, "3000", deposits=[{"rental_id": rental["id"], "amount": "2000"}]
    ).json()["payment"]
    notifications.run_checks(timezone.now())
    n = Notification.objects.get(event="rental.deposit_shortfall")
    assert n.params["missing"] == "3000.0000" or n.params["missing"].startswith("3000")

    # § 14.7.3: the receipt with a deposit part is reversed together with the part.
    reversed_ = post(
        owner, f"/api/c/{s.cmp}/money-records/{paid['id']}/reverse", {"reason": "Wrong"}, key()
    )
    assert reversed_.status_code == 201, reversed_.content
    deposit = _deposit(owner, s, rental["id"])
    assert (deposit["received"], deposit["held"], deposit["missing"]) == (
        "0.00",
        "0.00",
        "5000.00",
    )
    entry = DepositEntry.objects.get(kind="correction")
    assert entry.money_movement_id is not None and str(entry.money_movement_id) == paid["id"]
    balance = _balance(owner, s)
    assert balance["deposit_held"] in ({}, {"THB": "0.00"})
    assert balance["available"] in ({}, {"THB": "0.00"})

    # The full deposit ends the conflict notification.
    _receive(owner, s, account, "5000", deposits=[{"rental_id": rental["id"], "amount": "5000"}])
    notifications.run_checks(timezone.now())
    n.refresh_from_db()
    assert n.state != Notification.State.ACTIVE


def test_used_deposit_part_stops_the_reversal(owner: Client, s: Scenario) -> None:
    _with_deposit(owner, s)
    rental = _issue(owner, s)
    account = _account(owner, s)
    paid = _receive(
        owner, s, account, "2000", deposits=[{"rental_id": rental["id"], "amount": "2000"}]
    ).json()["payment"]
    moved = post(
        owner,
        f"/api/c/{s.cmp}/rentals/{rental['id']}/deposit/to-balance",
        {"amount": "500", "reason": "early"},
        key(),
    )
    assert moved.status_code == 200, moved.content
    refused = post(
        owner, f"/api/c/{s.cmp}/money-records/{paid['id']}/reverse", {"reason": "Wrong"}, key()
    )
    assert refused.json()["error"]["code"] == "DEPOSIT_PART_USED"


def test_overdue_return_goes_to_the_responsible_of_the_order(owner: Client, s: Scenario) -> None:
    manager, manager_id = hire(
        owner, s.cmp, "m@example.com", [{"type": "set", "set": "rental_manager"}]
    )
    ended = (timezone.now() - timedelta(hours=1)).isoformat()
    rental = _issue(manager, s, planned_end=ended)
    notifications.run_checks(timezone.now())
    # § 12.1.10: the employee who issued is the responsible of the order and is told first.
    assert _recipients("rental.return_overdue") == {manager_id}
    assert Rental.objects.get(pk=rental["id"]).state == "active"


def test_late_return_touching_the_next_booking_is_told(owner: Client, s: Scenario) -> None:
    ended = (timezone.now() + timedelta(hours=5)).isoformat()
    _issue(owner, s, planned_end=ended)
    start = timezone.now() + timedelta(hours=6)
    booked = post(
        owner,
        f"/api/c/{s.cmp}/bookings",
        {
            "vehicle_card_id": s.vehicle,
            "planned_start": start.isoformat(),
            "planned_end": (start + timedelta(days=1)).isoformat(),
        },
        key(),
    )
    assert booked.status_code == 201, booked.content
    rental = booked.json()["rental"]
    # Not yet late: no conflict.
    notifications.run_checks(timezone.now())
    assert not Notification.objects.filter(event="rental.schedule_conflict").exists()
    # The active rental is prolonged past the start of the booking: a schedule conflict.
    Rental.objects.filter(state="active").update(planned_end=start + timedelta(hours=2))
    notifications.run_checks(timezone.now())
    n = Notification.objects.get(event="rental.schedule_conflict")
    assert str(n.object_id) == rental["id"] and n.state == Notification.State.ACTIVE


def test_rejected_exception_creates_a_settlement_task(owner: Client, s: Scenario) -> None:
    rental = _issue(owner, s)
    stored = Rental.objects.get(pk=rental["id"])
    IssueException.objects.create(
        company_id=stored.company_id,
        rental=stored,
        state=IssueException.State.PENDING,
        mode=IssueException.Mode.AFTER_FACT,
        requirements=["payment"],
        reason="client paid later",
        created_by_user_id=stored.created_by_user_id,
    )
    decided = post(
        owner,
        f"/api/c/{s.cmp}/rentals/{rental['id']}/issue-exception/decide",
        {"decision": "reject", "reason": "Not allowed", "expected_version": stored.version},
        key(),
    )
    assert decided.status_code == 200, decided.content
    task = Task.objects.get(system_event="rental.exception_rejected")
    assert task.object_id == stored.id and task.state == "open"
