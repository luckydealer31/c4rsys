"""Charges of rentals and their services in the order by the known period, and the estimated
amounts of an order without a client (customer's decisions Р16, Р25 of 28.10.2026)."""

from collections.abc import Iterator
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from django.test import Client
from django.utils import timezone

from c4rsys.finance.models import ChargeEntry
from c4rsys.rentals import extra_lines as lines_module
from c4rsys.rentals import issue as issue_module
from c4rsys.rentals import returns as returns_module
from tests.accounts.helpers import post
from tests.companies.helpers import key
from tests.rentals.scenario import ISSUE_AT, NOW, Scenario, build, issue

pytestmark = pytest.mark.django_db


@pytest.fixture
def s(owner: Client) -> Scenario:
    return build(owner)


@pytest.fixture
def frozen() -> Iterator[None]:
    clock = SimpleNamespace(now=lambda: NOW)
    with (
        mock.patch.object(issue_module, "timezone", clock),
        mock.patch.object(returns_module, "timezone", clock),
        mock.patch.object(lines_module, "timezone", clock),
    ):
        yield


def _at(days: float) -> datetime:
    return timezone.now().replace(microsecond=0, second=0) + timedelta(days=days)


def _order(owner: Client, s: Scenario, order_id: str) -> dict[str, Any]:
    result: dict[str, Any] = owner.get(f"/api/c/{s.cmp}/orders/{order_id}").json()["order"]
    return result


def _charged(owner: Client, s: Scenario, order_id: str) -> str | None:
    amounts = _order(owner, s, order_id)["amounts"]
    return amounts["THB"]["charged"] if "THB" in amounts else None


def _base() -> Decimal:
    entries = ChargeEntry.objects.filter(source_type="rental_base")
    return sum((c.amount for c in entries), Decimal(0))


def _patch(owner: Client, s: Scenario, rental: dict[str, Any], **data: Any) -> Any:
    return owner.patch(
        f"/api/c/{s.cmp}/bookings/{rental['id']}",
        data={"expected_version": rental["version"], **data},
        content_type="application/json",
        headers=key(),
    )


def test_booking_is_estimated_without_client_and_charged_with_one(
    owner: Client, s: Scenario
) -> None:
    # Р25: without a client the order shows the estimated cost; with the client it is charged.
    # Р16: the booking's planned period is charged at once and follows its changes.
    body = {
        "vehicle_card_id": s.vehicle,
        "tariff_id": s.tariff,
        "planned_start": _at(1).isoformat(),
        "planned_end": _at(3).isoformat(),
    }
    rental = post(owner, f"/api/c/{s.cmp}/bookings", body, key()).json()["rental"]

    order = _order(owner, s, rental["order_id"])
    assert order["estimated"] == {"THB": "2000.00"}
    assert order["amounts"] == {}
    assert ChargeEntry.objects.count() == 0

    rental = _patch(owner, s, rental, client_id=s.client).json()["rental"]
    order = _order(owner, s, rental["order_id"])
    assert order["estimated"] == {}
    assert _charged(owner, s, rental["order_id"]) == "2000.00"

    rental = _patch(owner, s, rental, planned_end=_at(4).isoformat()).json()["rental"]
    assert _charged(owner, s, rental["order_id"]) == "3000.00"

    cancelled = post(
        owner,
        f"/api/c/{s.cmp}/bookings/{rental['id']}/cancel",
        {"reason": "Client called", "expected_version": rental["version"]},
        key(),
    )
    assert cancelled.status_code == 200, cancelled.content
    assert _base() == Decimal(0)  # a cancelled booking costs nothing; the entries stay
    assert ChargeEntry.objects.filter(source_type="rental_base").count() == 3


def test_issued_rental_is_charged_by_the_planned_end(
    owner: Client, s: Scenario, frozen: None
) -> None:
    # Р16: with a planned end the rental is charged at once by its planned period; the return
    # brings the charge to the actual period.
    planned_end = ISSUE_AT + timedelta(days=3)
    rental = issue(owner, s, planned_end=planned_end.isoformat()).json()["rental"]

    assert _charged(owner, s, rental["order_id"]) == "3000.00"
    assert rental["calculation"]["kind"] == "current"  # the rental card shows the cost up to now

    returned = post(
        owner,
        f"/api/c/{s.cmp}/rentals/{rental['id']}/return",
        {
            "actual_end": NOW.isoformat(),
            "return_location_id": s.location,
            "odometer": "10300",
            "fuel": "8",
            "expected_version": rental["version"],
        },
        key(),
    )
    assert returned.status_code == 200, returned.content
    assert _charged(owner, s, rental["order_id"]) == "2000.00"


def test_issued_rental_without_planned_end_waits_for_the_return(
    owner: Client, s: Scenario, frozen: None
) -> None:
    # Р16: without a planned end the cost is the current one, nothing is charged yet.
    rental = issue(owner, s).json()["rental"]

    assert _charged(owner, s, rental["order_id"]) is None
    assert _order(owner, s, rental["order_id"])["estimated"] == {}


def test_linked_service_is_charged_with_the_planned_period(
    owner: Client, s: Scenario, frozen: None
) -> None:
    # Р16: a linked time service follows the rental's planned period at once.
    service = post(
        owner,
        f"/api/c/{s.cmp}/extra-services",
        {"name": "Child seat", "pricing": "time", "unit": "day", "price": "100"},
        key(),
    ).json()["service"]["id"]
    planned_end = ISSUE_AT + timedelta(days=3)
    rental = issue(owner, s, planned_end=planned_end.isoformat()).json()["rental"]

    linked = post(
        owner,
        f"/api/c/{s.cmp}/rentals/{rental['id']}/services",
        {"service_id": service, "expected_version": rental["version"]},
        key(),
    )

    assert linked.status_code == 201, linked.content
    line = linked.json()["rental"]["services"][0]
    assert line["calculation"]["kind"] == "planned"
    assert line["calculation"]["amount"] == "300.00"
    assert _charged(owner, s, rental["order_id"]) == "3300.00"
