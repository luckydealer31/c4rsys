"""Requests to the client for rentals and their services: a direct issue into an order in work,
a service linked to an issued rental, a transfer to another order (ТЗ § 12.3, § 12.3.7,
§ 13.4.15; part А6.2)."""

from collections.abc import Iterator
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from django.test import Client
from django.utils import timezone

from c4rsys.orders.models import OrderApproval
from c4rsys.rentals import extra_lines as lines_module
from c4rsys.rentals import issue as issue_module
from tests.accounts.helpers import post
from tests.companies.helpers import key
from tests.rentals.scenario import NOW, Scenario, add_vehicle, build, issue

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def frozen_clock() -> Iterator[None]:
    clock = SimpleNamespace(now=lambda: NOW)
    with (
        mock.patch.object(issue_module, "timezone", clock),
        mock.patch.object(lines_module, "timezone", clock),
    ):
        yield


@pytest.fixture
def s(owner: Client) -> Scenario:
    return build(owner)


def _policy(owner: Client, s: Scenario) -> None:
    response = owner.put(
        f"/api/c/{s.cmp}/order-policy",
        data={"require_approval": True},
        content_type="application/json",
        headers=key(),
    )
    assert response.status_code == 200, response.content


def _in_work(owner: Client, s: Scenario) -> dict[str, Any]:
    _policy(owner, s)
    issued = issue(owner, s)
    assert issued.status_code == 201, issued.content
    # The first rental of the order is what the client asked for: nothing to agree.
    assert not OrderApproval.objects.exists()
    rental: dict[str, Any] = issued.json()["rental"]
    return rental


def _service(owner: Client, s: Scenario) -> str:
    body = {"name": "Child seat", "pricing": "time", "unit": "day", "price": "100"}
    created = post(owner, f"/api/c/{s.cmp}/extra-services", body, key())
    assert created.status_code == 201, created.content
    service: str = created.json()["service"]["id"]
    return service


def _link(owner: Client, s: Scenario, rental: dict[str, Any], service: str) -> dict[str, Any]:
    body = {"service_id": service, "expected_version": rental["version"]}
    linked = post(owner, f"/api/c/{s.cmp}/rentals/{rental['id']}/services", body, key())
    assert linked.status_code == 201, linked.content
    result: dict[str, Any] = linked.json()["rental"]
    return result


def test_refused_service_of_an_issued_rental_stays(owner: Client, s: Scenario) -> None:
    rental = _in_work(owner, s)
    rental = _link(owner, s, rental, _service(owner, s))
    line = rental["services"][0]
    assert line["state"] == "started"  # it follows the issued rental (decision У5)
    approval = OrderApproval.objects.get(item_type="extra_service", item_id=line["id"])
    body = {
        "decision": "declined",
        "method": "phone",
        "comment": "",
        "answered_at": (timezone.now() - timedelta(minutes=5)).isoformat(),
    }
    url = f"/api/c/{s.cmp}/orders/{rental['order_id']}/approvals/{approval.id}/answer"
    answered = post(owner, url, body, key())
    # § 12.3.1: a started position stays with what was done; the refusal is recorded.
    assert answered.status_code == 200, answered.content
    approval.refresh_from_db()
    assert approval.state == "declined"
    found = owner.get(f"/api/c/{s.cmp}/rentals/{rental['id']}").json()["rental"]
    assert found["services"][0]["state"] == "started"


def test_direct_issue_waits_and_moves_with_the_transfer(owner: Client, s: Scenario) -> None:
    first = _in_work(owner, s)
    order_id = first["order_id"]
    second_vehicle = add_vehicle(owner, s, s.location, name="Honda Click")
    issued = issue(owner, s, vehicle_card_id=second_vehicle, order_id=order_id)
    assert issued.status_code == 201, issued.content
    second = issued.json()["rental"]
    approval = OrderApproval.objects.get(item_type="rental", item_id=second["id"])
    assert approval.state == "pending" and str(approval.order_id) == order_id
    second = _link(owner, s, second, _service(owner, s))
    service_approval = OrderApproval.objects.get(
        item_type="extra_service", item_id=second["services"][0]["id"]
    )

    moved = post(
        owner,
        f"/api/c/{s.cmp}/rentals/{second['id']}/transfer",
        {"order_id": None, "expected_version": second["version"]},
        key(),
    )
    assert moved.status_code == 200, moved.content
    target = moved.json()["rental"]["order_id"]
    assert target != order_id
    # § 12.3.7: the requests go with the rental and its services.
    approval.refresh_from_db()
    service_approval.refresh_from_db()
    assert str(approval.order_id) == target
    assert str(service_approval.order_id) == target
