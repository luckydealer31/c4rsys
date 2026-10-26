"""Free time, mileage overage and manual charges of a rental (ТЗ § 13.3, § 13.4.1–13.4.4,
§ 13.5.1; customer's decisions Т2–Т6 of 27.10.2026)."""

from collections.abc import Iterator
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from django.db import DatabaseError, transaction
from django.test import Client

from c4rsys.finance.models import ChargeEntry
from c4rsys.rentals import free_time as free_time_module
from c4rsys.rentals import issue as issue_module
from c4rsys.rentals import returns as returns_module
from c4rsys.rentals.models import RentalManualCharge
from tests.accounts.helpers import post
from tests.companies.helpers import hire, key
from tests.rentals.scenario import ISSUE_AT, NOW, Scenario, build, issue

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def frozen_clock() -> Iterator[None]:
    clock = SimpleNamespace(now=lambda: NOW)
    with (
        mock.patch.object(issue_module, "timezone", clock),
        mock.patch.object(returns_module, "timezone", clock),
        mock.patch.object(free_time_module, "timezone", clock),
    ):
        yield


@pytest.fixture
def s(owner: Client) -> Scenario:
    return build(owner)


def _rental(owner: Client, s: Scenario, rental_id: str) -> dict[str, Any]:
    result: dict[str, Any] = owner.get(f"/api/c/{s.cmp}/rentals/{rental_id}").json()["rental"]
    return result


def _free(owner: Client, s: Scenario, rental: dict[str, Any], **data: Any) -> Any:
    body = {"expected_version": rental["version"], **data}
    return post(owner, f"/api/c/{s.cmp}/rentals/{rental['id']}/free-time", body, key())


def _return(owner: Client, s: Scenario, rental: dict[str, Any], **data: Any) -> Any:
    body = {
        "actual_end": NOW.isoformat(),
        "return_location_id": s.location,
        "odometer": "10300",
        "fuel": "8",
        "expected_version": rental["version"],
        **data,
    }
    return post(owner, f"/api/c/{s.cmp}/rentals/{rental['id']}/return", body, key())


def _charges(source_type: str) -> Decimal:
    return sum((c.amount for c in ChargeEntry.objects.filter(source_type=source_type)), Decimal(0))


def test_free_day_lowers_the_cost_but_not_the_period(owner: Client, s: Scenario) -> None:
    # § 13.3.1–13.3.2: one free day by the number of units; two days of rental cost one.
    rental = issue(owner, s).json()["rental"]

    added = _free(owner, s, rental, start=(ISSUE_AT + timedelta(hours=1)).isoformat(), units=1)

    assert added.status_code == 201, added.content
    rental = added.json()["rental"]
    interval = rental["free_intervals"][0]
    assert datetime.fromisoformat(interval["end"]) == ISSUE_AT + timedelta(hours=25)
    returned = _return(owner, s, rental).json()["rental"]
    assert returned["calculation"]["units"] == 1
    assert returned["calculation"]["free_minutes"] == 24 * 60
    assert datetime.fromisoformat(returned["actual_start"]) == ISSUE_AT
    assert _charges("rental_base") == Decimal("1000")


def test_free_time_stays_inside_the_rental(owner: Client, s: Scenario) -> None:
    # § 13.3.2, Т4: without a planned end, up to now; nothing is cut automatically.
    rental = issue(owner, s).json()["rental"]

    before_start = _free(
        owner, s, rental, start=(ISSUE_AT - timedelta(hours=1)).isoformat(), units=1
    )
    after_now = _free(
        owner, s, rental, start=NOW.isoformat(), end=(NOW + timedelta(hours=1)).isoformat()
    )
    inside = _free(
        owner,
        s,
        rental,
        start=(NOW - timedelta(hours=3)).isoformat(),
        end=(NOW - timedelta(hours=1)).isoformat(),
    )

    assert before_start.json()["error"]["code"] == "FREE_INTERVAL_OUTSIDE_RENTAL"
    assert after_now.json()["error"]["code"] == "FREE_INTERVAL_OUTSIDE_RENTAL"
    assert inside.status_code == 201, inside.content
    early = _return(
        owner, s, inside.json()["rental"], actual_end=(NOW - timedelta(hours=2)).isoformat()
    )
    assert early.json()["error"]["code"] == "FREE_INTERVAL_OUTSIDE_RENTAL"


def test_mileage_overage_is_its_own_charge(owner: Client, s: Scenario) -> None:
    # § 13.4.1–13.4.4: 100 km per day included, 5 THB per km over; two days, 300 km driven.
    owner.put(
        f"/api/c/{s.cmp}/tariffs/{s.tariff}/vehicles/{s.vehicle}",
        data={
            "mileage": {"mode": "included", "included_per_unit": "100", "overage_price": "5"},
            "deposit": {"required": False},
        },
        content_type="application/json",
        headers=key(),
    )
    rental = issue(owner, s).json()["rental"]

    returned = _return(owner, s, rental).json()["rental"]

    assert returned["mileage"]["distance"] == "300.0"
    assert returned["mileage"]["included"] == "200.0"
    assert returned["mileage"]["amount"] == "500.00"
    assert _charges("rental_mileage") == Decimal("500")
    assert _charges("rental_base") == Decimal("2000")


def test_manual_charge_and_its_cancellation(owner: Client, s: Scenario) -> None:
    # § 13.5.1, Т6: in the tariff's currency with a reason; cancelled by a reversing entry.
    rental = issue(owner, s).json()["rental"]
    url = f"/api/c/{s.cmp}/rentals/{rental['id']}/manual-charges"

    added = post(
        owner,
        url,
        {"kind": "fuel", "amount": "350", "reason": "Tank half empty", "expected_version": 1},
        key(),
    )

    assert added.status_code == 201, added.content
    rental = added.json()["rental"]
    charge = rental["manual_charges"][0]
    assert (charge["kind"], charge["amount"], charge["currency"]) == ("fuel", "350.0000", "THB")
    assert _charges("rental_manual") == Decimal("350")
    cancelled = post(
        owner,
        f"{url}/{charge['id']}/cancel",
        {"reason": "Mistake", "expected_version": rental["version"]},
        key(),
    )
    assert cancelled.status_code == 200, cancelled.content
    assert cancelled.json()["rental"]["manual_charges"][0]["cancel_reason"] == "Mistake"
    assert _charges("rental_manual") == Decimal("0")
    assert ChargeEntry.objects.filter(source_type="rental_manual").count() == 2
    with pytest.raises(DatabaseError), transaction.atomic():
        RentalManualCharge.objects.all().delete()


def test_rights_of_free_time_and_manual_charges(owner: Client, s: Scenario) -> None:
    # Т2: the rights of the rental; Т6: the rental manager charges, the driver does nothing.
    rental = issue(owner, s).json()["rental"]
    manager, _ = hire(owner, s.cmp, "m@example.com", [{"type": "set", "set": "rental_manager"}])
    driver, _ = hire(owner, s.cmp, "d@example.com", [{"type": "set", "set": "driver"}])
    body = {"kind": "other", "amount": "100", "reason": "Cleaning", "expected_version": 1}
    url = f"/api/c/{s.cmp}/rentals/{rental['id']}/manual-charges"

    assert post(driver, url, body, key()).status_code == 403
    assert _free(driver, s, rental, start=ISSUE_AT.isoformat(), units=1).status_code == 403
    assert post(manager, url, body, key()).status_code == 201
