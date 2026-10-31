"""The system work «Odometer replacement» and the change of the odometer unit (ТЗ § 8.2.10,
§ 8.2.12–8.2.13, § 9.1.10, § 13.4.3)."""

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from django.test import Client

from c4rsys.fleet import odometer
from c4rsys.fleet.models import OdometerReading, OdometerUnitChange, VehicleCard
from c4rsys.platform.commands import Actor, CommandContext
from c4rsys.rentals.models import Rental, TariffVehicleTerms
from c4rsys.service.models import MaintenanceRule
from tests.accounts.helpers import post
from tests.companies.helpers import hire, key
from tests.rentals.scenario import ISSUE_AT, NOW, Scenario, issue
from tests.service.test_service import _at, _code, _do, _job, _ok, _op, _work

pytestmark = pytest.mark.django_db

GRANT = {"type": "grant", "locations": "all", "contexts": "all", "cash_accounts": "all"}


def _done(owner: Client, s: Scenario, work: dict[str, Any], end: float, **result: Any) -> Any:
    op = _op(owner, s, [work["id"]])
    op = _ok(_do(owner, s, op, "accept", accepted_at=_at(end - 2)))
    op = _ok(_job(owner, s, op["jobs"][0], "start", actual_start=_at(end - 1)))
    return _job(owner, s, op["jobs"][0], "complete", actual_end=_at(end), **result)


def _vehicle(owner: Client, s: Scenario) -> dict[str, Any]:
    body: dict[str, Any] = owner.get(f"/api/c/{s.cmp}/vehicles/{s.vehicle}").json()["vehicle"]
    return body


def test_replacement_keeps_the_accumulated_mileage(owner: Client, s: Scenario) -> None:
    oil = _work(owner, s, system_work="engine_oil")
    swap = _work(owner, s, name="Odometer", system_work="odometer_replacement")
    assert _done(owner, s, oil, -10, odometer="10400").status_code == 200
    # The system work takes the two readings instead of one.
    missing = _done(owner, s, swap, -8, odometer="0")
    assert missing.json()["error"]["field"] == "odometer_replacement"
    preview = owner.get(
        f"/api/c/{s.cmp}/vehicles/{s.vehicle}/odometer/replacement-preview",
        {"at": _at(-6), "old_final": "10500", "new_start": "3"},
    ).json()["preview"]
    assert preview == {"accumulated": "10500.0", "unit": "km", "conflicts": []}
    wrong = owner.get(
        f"/api/c/{s.cmp}/vehicles/{s.vehicle}/odometer/replacement-preview",
        {"at": _at(-6), "old_final": "10000", "new_start": "3"},
    ).json()["preview"]
    assert wrong["conflicts"][0]["value"] == "10400.0"
    done = _done(
        owner,
        s,
        swap,
        -6,
        odometer_replacement={"old_final": "10500", "new_start": "3"},
    )
    assert done.status_code == 200, done.content
    current = _vehicle(owner, s)["odometer"]
    assert (current["value"], current["accumulated"]) == ("3.0", "10500.0")
    # A reading of the new odometer below the old ones is no conflict (§ 8.2.12–8.2.13).
    assert _done(owner, s, oil, -4, odometer="103").status_code == 200
    current = _vehicle(owner, s)["odometer"]
    assert (current["value"], current["accumulated"]) == ("103.0", "10600.0")
    # Below the start of the new odometer it is.
    low = _done(owner, s, oil, -2, odometer="1")
    assert _code(low) == "ODOMETER_SEQUENCE_CONFLICT"
    # An ordinary work named like it does not reset anything.
    named = _work(owner, s, name="Odometer replacement")
    assert _code(_done(owner, s, named, -1, odometer="0")) == "ODOMETER_SEQUENCE_CONFLICT"
    replacements = _vehicle(owner, s)["odometer_replacements"]
    assert [(r["old_final"], r["new_start"], r["accumulated"]) for r in replacements] == [
        ("10500.0", "3.0", "10500.0")
    ]


def test_maintenance_counts_by_the_accumulated_mileage(owner: Client, s: Scenario) -> None:
    oil = _work(owner, s, system_work="engine_oil")
    swap = _work(owner, s, name="Odometer", system_work="odometer_replacement")
    assert _done(owner, s, oil, -10, odometer="10000").status_code == 200
    rules_url = f"/api/c/{s.cmp}/service/vehicles/{s.vehicle}/maintenance"
    added = post(
        owner,
        rules_url,
        {"name": "Oil", "system_work": "engine_oil", "interval_distance": 1000},
        key(),
    )
    assert added.status_code == 201, added.content
    _done(owner, s, swap, -6, odometer_replacement={"old_final": "10900", "new_start": "0"})
    _done(owner, s, _work(owner, s, name="Wash"), -4, odometer="50")
    status = owner.get(rules_url).json()["rules"][0]["status"]
    # 10 000 + 1 000 is due; the accumulated 10 950 is before it although the dashboard says 50.
    assert status["due_odometer"] == "11000"
    assert status["state"] == "soon" or status["state"] == "ok"
    _done(owner, s, _work(owner, s, name="Wash 2"), -2, odometer="150")
    assert owner.get(rules_url).json()["rules"][0]["status"]["state"] == "required"


def _ctx(s: Scenario) -> CommandContext:
    return CommandContext(actor=Actor(user_id=uuid4()), command="test", idempotency_key="k")


def test_rental_mileage_over_a_replacement(owner: Client, s: Scenario) -> None:
    owner.put(
        f"/api/c/{s.cmp}/tariffs/{s.tariff}/vehicles/{s.vehicle}",
        data={
            "mileage": {"mode": "included", "included_per_unit": "50", "overage_price": "10"},
            "deposit": {"required": False},
        },
        content_type="application/json",
        headers=key(),
    )
    issued = issue(owner, s, actual_start=ISSUE_AT.isoformat(), odometer="10000")
    assert issued.status_code == 201, issued.content
    rental = issued.json()["rental"]
    card = VehicleCard.objects.get(pk=s.vehicle)
    odometer.record_replacement(
        _ctx(s),
        card,
        replaced_at=ISSUE_AT + timedelta(days=1),
        old_final=Decimal("10100"),
        new_start=Decimal("0"),
        source_type="test",
        source_id=None,
    )
    from types import SimpleNamespace
    from unittest import mock

    from c4rsys.rentals import returns as returns_module

    clock = SimpleNamespace(now=lambda: NOW)
    with mock.patch.object(returns_module, "timezone", clock):
        returned = post(
            owner,
            f"/api/c/{s.cmp}/rentals/{rental['id']}/return",
            {
                "actual_end": NOW.isoformat(),
                "return_location_id": s.location,
                "odometer": "50",
                "fuel": "8",
                "expected_version": rental["version"],
            },
            key(),
        )
    assert returned.status_code == 200, returned.content
    mileage = owner.get(f"/api/c/{s.cmp}/rentals/{rental['id']}").json()["rental"]["mileage"]
    # § 13.4.3: (10 100 − 10 000) + (50 − 0) by the accumulated mileage.
    assert mileage["distance"] == "150.0"
    assert mileage["included"] == "100.0"
    assert mileage["overage"] == "50.0"


def test_unit_change_converts_everything_or_only_the_label(owner: Client, s: Scenario) -> None:
    owner.put(
        f"/api/c/{s.cmp}/tariffs/{s.tariff}/vehicles/{s.vehicle}",
        data={
            "mileage": {"mode": "included", "included_per_unit": "100", "overage_price": "10"},
            "deposit": {"required": False},
        },
        content_type="application/json",
        headers=key(),
    )
    _done(owner, s, _work(owner, s, system_work="engine_oil"), -6, odometer="16093.4")
    post(
        owner,
        f"/api/c/{s.cmp}/service/vehicles/{s.vehicle}/maintenance",
        {"name": "Oil", "system_work": "engine_oil", "interval_distance": 10000},
        key(),
    )
    url = f"/api/c/{s.cmp}/vehicles/{s.vehicle}/odometer-unit"
    clerk, _ = hire(
        owner,
        s.cmp,
        "clerk@example.com",
        [{**GRANT, "permission": p} for p in ("vehicle.view", "vehicle.edit")],
    )
    card = _vehicle(owner, s)
    body = {"to_unit": "mi", "method": "convert", "expected_version": card["version"]}
    assert post(clerk, url, body, key()).status_code == 403
    preview = owner.get(f"{url}/preview", {"to_unit": "mi", "method": "convert"}).json()["preview"]
    assert preview["changes"]["vehicle"][0]["after"] == "10000.0 mi"
    assert preview["changes"]["rentals"][0]["after"] == "62.1/16.0934 mi"
    assert post(owner, url, {**body, "method": "guess"}, key()).json()["error"]["field"] == "method"
    changed = post(owner, url, body, key())
    assert changed.status_code == 200, changed.content
    assert changed.json()["vehicle"]["odometer_unit"] == "mi"
    reading = OdometerReading.objects.get()
    assert (reading.value, reading.unit) == (Decimal("10000.0"), "mi")
    assert MaintenanceRule.objects.get().interval_distance == 6214
    terms = TariffVehicleTerms.objects.get()
    assert (terms.included_per_unit, terms.overage_price, terms.mileage_unit) == (
        Decimal("62.1"),
        Decimal("16.0934"),
        "mi",
    )
    change = OdometerUnitChange.objects.get()
    assert change.method == "convert" and change.details["vehicle"][0]["before"] == "16093.4 km"
    # The label only: the numbers stay.
    card = changed.json()["vehicle"]
    relabel = post(
        owner,
        url,
        {"to_unit": "km", "method": "relabel", "expected_version": card["version"]},
        key(),
    )
    assert relabel.status_code == 200, relabel.content
    reading.refresh_from_db()
    assert (reading.value, reading.unit) == (Decimal("10000.0"), "km")
    assert [h["method"] for h in relabel.json()["vehicle"]["odometer_unit_history"]] == [
        "relabel",
        "convert",
    ]
    assert not Rental.objects.exists()
