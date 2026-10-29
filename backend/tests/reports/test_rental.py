"""Reports, part О3: «Rental and fleet» (ТЗ § 21.1.3, § 21.4.1–21.4.2; decisions О-3, О-6)."""

from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from django.test import Client

from c4rsys.reports import rental as rental_report
from tests.orders.test_close import _finished_rental, _pay, desk, frozen_clock  # noqa: F401
from tests.rentals.scenario import NOW, Scenario, add_location, add_vehicle

pytestmark = pytest.mark.django_db

PERIOD = "start=2026-11-01&end=2026-11-03"


@pytest.fixture(autouse=True)
def report_clock() -> Iterator[None]:
    """The report is read a day after the return of the scenario."""
    with mock.patch.object(
        rental_report, "timezone", SimpleNamespace(now=lambda: NOW + timedelta(days=1))
    ):
        yield


def _blocks(client: Client, s: Scenario, query: str = PERIOD) -> dict[str, dict[str, Any]]:
    response = client.get(f"/api/c/{s.cmp}/reports/rental?{query}")
    assert response.status_code == 200, response.content
    return {b["code"]: b for b in response.json()["blocks"]}


def test_rental_time_utilisation_and_days(owner: Client, s: Scenario) -> None:
    _finished_rental(owner, s)  # 1 Nov 10:00 → 3 Nov 10:00, Bangkok
    blocks = _blocks(owner, s)
    assert blocks["rental.started"]["value"] == 1 and blocks["rental.finished"]["value"] == 1
    assert blocks["rental.hours"]["value"] == "48.0"
    # Accounting time 1–3 Nov = 72 h; rented 48 h; idle 24 h (§ 21.4.1).
    assert blocks["fleet.utilization"]["value"] == "66.7"
    assert blocks["fleet.idle_hours"]["value"] == "24.0"
    row = blocks["fleet.by_vehicle"]["rows"][0]
    assert row["rentals"] == 1 and row["rental_hours"] == "48.0" and row["mileage"] == "300.0"
    # § 21.1.3: the base cost by days of use: 14 h, 24 h, 10 h of 48 h.
    days = blocks["rental.daily_cost"]["rows"]
    assert [d["day"] for d in days] == ["2026-11-01", "2026-11-02", "2026-11-03"]
    assert not any(d["preliminary"] for d in days)
    amounts = [Decimal(d["amount"]) for d in days]
    total = sum(amounts, Decimal(0))
    assert total > 0
    assert abs(amounts[1] - total * 24 / 48) <= Decimal("0.02")
    assert abs(amounts[0] - total * 14 / 48) <= Decimal("0.02")


def test_filters_by_location_and_vehicle(owner: Client, s: Scenario) -> None:
    other = add_location(owner, s.cmp, "Kata")
    add_vehicle(owner, s, other, "Honda Jazz")
    _finished_rental(owner, s)
    kata = _blocks(owner, s, f"{PERIOD}&location={other}")
    assert kata["rental.started"]["value"] == 0
    assert [r["vehicle"] for r in kata["fleet.by_vehicle"]["rows"]] == ["Honda Jazz"]
    one = _blocks(owner, s, f"{PERIOD}&vehicle={s.vehicle}")
    assert [r["vehicle"] for r in one["fleet.by_vehicle"]["rows"]] == ["Toyota Yaris"]


def test_payback_needs_the_cost_of_acquisition(owner: Client, s: Scenario, desk: str) -> None:  # noqa: F811
    rental = _finished_rental(owner, s)
    assert _pay(owner, s, desk, rental["order_id"], "1000").status_code == 201
    row = _blocks(owner, s)["fleet.payback"]["rows"][0]
    assert row["state"] == "not_enough_data"
    card = owner.get(f"/api/c/{s.cmp}/vehicles/{s.vehicle}").json()["vehicle"]
    saved = owner.put(
        f"/api/c/{s.cmp}/vehicles/{s.vehicle}/acquisition",
        data={"cost": "300000", "currency": "THB", "expected_version": card["version"]},
        content_type="application/json",
        headers={"Idempotency-Key": "acq-1"},
    )
    assert saved.status_code == 200, saved.content
    assert saved.json()["vehicle"]["acquisition"] == {"cost": "300000.0000", "currency": "THB"}
    row = _blocks(owner, s)["fleet.payback"]["rows"][0]
    # The payment of the order goes to the vehicle by its share of the order's charges.
    assert row["received"] == "1000.00" and row["acquisition"] == "300000.00"
    assert row["state"] == "not_paid_back"
