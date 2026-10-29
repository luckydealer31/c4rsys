"""Service С5: periodic maintenance rules of a vehicle and the history of its works (ТЗ гл. 9)."""

from collections.abc import Iterator
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from django.test import Client

from c4rsys.service import maintenance as maintenance_module
from tests.accounts.helpers import post
from tests.companies.helpers import hire, key
from tests.rentals.scenario import BKK, NOW, Scenario, add_vehicle
from tests.service.test_service import _at, _code, _do, _job, _ok, _op, _work

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def today() -> Iterator[None]:
    def localdate(value: datetime | None = None, timezone: Any = None) -> Any:
        return (value or NOW).astimezone(BKK).date()

    with mock.patch.object(
        maintenance_module, "timezone", SimpleNamespace(now=lambda: NOW, localdate=localdate)
    ):
        yield


def _rules(client: Client, s: Scenario, vehicle: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = client.get(
        f"/api/c/{s.cmp}/service/vehicles/{vehicle or s.vehicle}/maintenance"
    ).json()
    return body


def _add_rule(client: Client, s: Scenario, vehicle: str | None = None, **body: Any) -> Any:
    data = {"name": "Oil", "system_work": "engine_oil", "interval_days": 180, **body}
    return post(
        client, f"/api/c/{s.cmp}/service/vehicles/{vehicle or s.vehicle}/maintenance", data, key()
    )


def _status(response: Any, index: int = 0) -> dict[str, Any]:
    assert response.status_code in (200, 201), response.content
    status: dict[str, Any] = response.json()["rules"][index]["status"]
    return status


def _service_done(
    owner: Client, s: Scenario, work: dict[str, Any], odometer: str, end: float = -1
) -> dict[str, Any]:
    op = _op(owner, s, [work["id"]])
    op = _ok(_do(owner, s, op, "accept", accepted_at=_at(end - 2)))
    op = _ok(_job(owner, s, op["jobs"][0], "start", actual_start=_at(end - 1)))
    op = _ok(_job(owner, s, op["jobs"][0], "complete", actual_end=_at(end), odometer=odometer))
    return _ok(_do(owner, s, op, "finish"))


def test_count_from_manual_last_then_from_done_work(owner: Client, s: Scenario) -> None:
    rule = _add_rule(
        owner,
        s,
        interval_distance=5000,
        remind_days=14,
        last_done_on="2026-05-10",
        last_done_odometer="5000",
    )
    status = _status(rule)
    # 10.05 + 180 days = 06.11; on 03.11 it is within the 14 days of the reminder (§ 9.1.10).
    assert status["base"] == "manual_last" and status["due_on"] == "2026-11-06"
    assert status["due_odometer"] == "10000" and status["state"] == "soon"

    oil = _work(owner, s, system_work="engine_oil")
    _service_done(owner, s, oil, "10500")
    status = _status(owner.get(f"/api/c/{s.cmp}/service/vehicles/{s.vehicle}/maintenance"))
    # The done work restarts the count from its actual date and odometer (§ 9.1.11).
    assert status["base"] == "work" and status["base_odometer"] == "10500"
    assert status["due_on"] == "2027-05-02" and status["due_odometer"] == "15500"
    assert status["state"] == "ok"

    history = owner.get(f"/api/c/{s.cmp}/service/vehicles/{s.vehicle}/history").json()["jobs"]
    assert [j["odometer"] for j in history] == ["10500"]


def test_distance_and_first_of_two(owner: Client, s: Scenario) -> None:
    oil = _work(owner, s, system_work="engine_oil")
    _service_done(owner, s, oil, "10000", end=-5)
    rule = _add_rule(owner, s, interval_days=365, interval_distance=400)
    assert _status(rule)["state"] == "ok"
    # A later reading beyond the distance: required whatever the date (§ 9.1.9).
    _service_done(owner, s, _work(owner, s, name="Wash", price="100"), "10450")
    status = _status(owner.get(f"/api/c/{s.cmp}/service/vehicles/{s.vehicle}/maintenance"))
    assert status["state"] == "required"
    due = owner.get(f"/api/c/{s.cmp}/service/maintenance-due").json()["due"]
    assert [d["vehicle_card_id"] for d in due] == [s.vehicle]


def test_unlinked_rule_and_manual_next(owner: Client, s: Scenario) -> None:
    own = _add_rule(owner, s, name="Check chain", system_work="", next_due_on="2026-11-01")
    status = _status(own)
    assert status["base"] == "manual_next" and status["state"] == "required"
    # A work with a matching name is not recognized without a link (§ 9.1.5, § 9.1.14).
    _service_done(owner, s, _work(owner, s, name="Check chain", price="100"), "10000")
    assert (
        _status(owner.get(f"/api/c/{s.cmp}/service/vehicles/{s.vehicle}/maintenance"))["base"]
        == "manual_next"
    )
    assert _code(_add_rule(owner, s, interval_days=None)) == "MAINTENANCE_INTERVAL_REQUIRED"


def test_copy_keeps_or_replaces_only_by_choice(owner: Client, s: Scenario) -> None:
    other = add_vehicle(owner, s, s.location, name="Honda Click")
    _add_rule(owner, s, interval_days=180, last_done_on="2026-05-10")
    _add_rule(owner, s, name="Brake fluid", system_work="brake_fluid", interval_days=730)
    _add_rule(owner, s, vehicle=other, interval_days=90)
    url = f"/api/c/{s.cmp}/service/vehicles/{other}/maintenance/copy"
    refused = post(owner, url, {"from_vehicle_id": s.vehicle}, key())
    assert _code(refused) == "MAINTENANCE_COPY_CONFLICTS"
    conflict = refused.json()["error"]["params"]["conflicts"][0]["rule"]

    kept = post(owner, url, {"from_vehicle_id": s.vehicle, "choices": {conflict: "keep"}}, key())
    assert kept.status_code == 200, kept.content
    rules = {r["name"]: r for r in kept.json()["rules"]}
    # Missing rules are added without the history and the initial data (§ 9.1.12).
    assert rules["Oil"]["interval_days"] == 90
    assert rules["Brake fluid"]["last_done_on"] is None

    _add_rule(owner, s, vehicle=other, name="Wash", system_work="", interval_days=7)
    replaced = post(
        owner, url, {"from_vehicle_id": s.vehicle, "choices": {conflict: "replace"}}, key()
    )
    assert _code(replaced) == "MAINTENANCE_COPY_CONFLICTS"  # the brake fluid matches now


def test_rules_need_rights(owner: Client, s: Scenario) -> None:
    viewer, _ = hire(
        owner, s.cmp, "viewer@example.com", [{"type": "grant", "permission": "vehicle.view"}]
    )
    _add_rule(owner, s)
    assert len(_rules(viewer, s)["rules"]) == 1
    assert _add_rule(viewer, s).status_code == 403
    assert viewer.get(f"/api/c/{s.cmp}/service/vehicles/{s.vehicle}/history").status_code == 403
