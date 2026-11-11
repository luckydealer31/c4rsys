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


def test_rules_and_records_are_separate_rights(owner: Client, s: Scenario) -> None:
    # § 23.5.1 (block 14): the rule and a recorded done work are separate from vehicle.edit.
    def grant(code: str) -> dict[str, str]:
        return {"type": "grant", "permission": code}

    editor, _ = hire(owner, s.cmp, "ed@example.com", [grant("vehicle.edit")])
    assert _add_rule(editor, s).status_code == 403
    rules, _ = hire(owner, s.cmp, "ru@example.com", [grant("vehicle.maintenance.rules")])
    recorder, _ = hire(owner, s.cmp, "re@example.com", [grant("vehicle.maintenance.record")])
    created = _add_rule(rules, s)
    assert created.status_code == 201, created.content
    rule = created.json()["rules"][0]
    base = {"name": "Oil", "system_work": "engine_oil", "interval_days": 180}

    def change(client: Client, version: int, **body: Any) -> Any:
        return client.patch(
            f"/api/c/{s.cmp}/service/maintenance/{rule['id']}",
            data={**base, "expected_version": version, **body},
            content_type="application/json",
            headers=key(),
        )

    # A recorded done work restarts the countdown; the right of the rules does not give it.
    assert change(rules, rule["version"], last_done_on="2026-10-01").status_code == 403
    assert change(recorder, rule["version"], interval_days=90).status_code == 403
    recorded = change(recorder, rule["version"], last_done_on="2026-10-01")
    assert recorded.status_code == 200, recorded.content
    version = recorded.json()["rules"][0]["version"]
    kept = {"last_done_on": "2026-10-01"}
    assert change(rules, version, interval_days=90, **kept).status_code == 200
    assert change(editor, version + 1, interval_days=60, **kept).status_code == 403


def test_one_rule_per_service_operation(owner: Client, s: Scenario) -> None:
    """Customer's decision of 11.11.2026: a vehicle has one active rule per operation."""
    first = _add_rule(owner, s)
    assert first.status_code == 201, first.content
    rule = first.json()["rules"][0]

    # The same system work; the same name in another case and with extra spaces; an own name
    # equal to the name of the work the first rule is linked to.
    assert _code(_add_rule(owner, s, name="Another")) == "MAINTENANCE_RULE_DUPLICATE"
    assert (
        _code(_add_rule(owner, s, name="  oIL ", system_work=None)) == "MAINTENANCE_RULE_DUPLICATE"
    )
    assert (
        _code(_add_rule(owner, s, name="engine  OIL change", system_work=None))
        == "MAINTENANCE_RULE_DUPLICATE"
    )
    own = _add_rule(owner, s, name="Wash", system_work=None)
    assert own.status_code == 201, own.content
    assert len(_rules(owner, s)["rules"]) == 2

    # A change must not make a double either; another vehicle has its own rules.
    wash = next(r for r in own.json()["rules"] if r["name"] == "Wash")
    url = f"/api/c/{s.cmp}/service/maintenance/{wash['id']}"
    body = {"name": "OIL", "interval_days": 30, "expected_version": wash["version"]}
    changed = owner.patch(url, body, content_type="application/json", headers=key())
    assert _code(changed) == "MAINTENANCE_RULE_DUPLICATE"
    other = add_vehicle(owner, s, s.location, name="Second")
    assert _add_rule(owner, s, vehicle=other).status_code == 201

    # An archived rule frees the operation; it cannot be restored over a new active one.
    url = f"/api/c/{s.cmp}/service/maintenance/{rule['id']}"
    body = {"archived": True, "expected_version": rule["version"]}
    archived = owner.patch(url, body, content_type="application/json", headers=key())
    assert archived.status_code == 200, archived.content
    assert _add_rule(owner, s).status_code == 201
    body = {"archived": False, "expected_version": rule["version"] + 1}
    restored = owner.patch(url, body, content_type="application/json", headers=key())
    assert _code(restored) == "MAINTENANCE_RULE_DUPLICATE"
