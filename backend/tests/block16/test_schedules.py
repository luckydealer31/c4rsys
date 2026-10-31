"""Block 16 B: schedules of locations and contexts (ТЗ § 4.2.3, § 4.2.4, § 22.1.6)."""

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from django.test import Client
from django.utils import timezone

from c4rsys.companies.models import WEEKDAYS
from c4rsys.platform.models import AuditEvent
from c4rsys.rentals.models import Rental
from tests.accounts.helpers import post
from tests.companies.helpers import hire, key
from tests.rentals.scenario import Scenario, build

pytestmark = pytest.mark.django_db
BKK = ZoneInfo("Asia/Bangkok")


@pytest.fixture
def s(owner: Client) -> Scenario:
    scenario = build(owner)
    post(
        owner,
        f"/api/c/{scenario.cmp}/locations/{scenario.location}/contexts",
        {"type": "service"},
        key(),
    )
    return scenario


def _day() -> date:
    return timezone.now().astimezone(BKK).date() + timedelta(days=3)


def _local(hour: int, day: date | None = None) -> str:
    return datetime.combine(day or _day(), time(hour), tzinfo=BKK).isoformat()


def _week(intervals: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    return {d: list(intervals) for d in WEEKDAYS}


def _location(client: Client, s: Scenario) -> dict[str, Any]:
    found: dict[str, Any] = client.get(f"/api/c/{s.cmp}/locations/{s.location}").json()["location"]
    return found


def _put(client: Client, path: str, body: dict[str, Any]) -> Any:
    return client.put(path, data=body, content_type="application/json", headers=key())


def _set(client: Client, s: Scenario, weekly: Any, exceptions: Any = (), version: int = 1) -> Any:
    body = {"weekly": weekly, "exceptions": list(exceptions), "expected_version": version}
    return _put(client, f"/api/c/{s.cmp}/locations/{s.location}/schedule", body)


def _context(location: dict[str, Any], kind: str) -> dict[str, Any]:
    found: dict[str, Any] = next(c for c in location["contexts"] if c["type"] == kind)
    return found


def test_location_schedule_is_changed_with_conflicts_and_journal(
    owner: Client, s: Scenario
) -> None:
    location = _location(owner, s)
    assert location["schedule"]["version"] == 1
    assert location["schedule"]["weekly"]["mon"] == [{"from": "00:00", "to": "24:00"}]
    # A booking issued at night and returned in the day.
    booked = post(
        owner,
        f"/api/c/{s.cmp}/bookings",
        {
            "vehicle_card_id": s.vehicle,
            "issue_location_id": s.location,
            "planned_start": _local(3),
            "planned_end": _local(12, _day() + timedelta(days=1)),
        },
        key(),
    )
    assert booked.status_code == 201, booked.content
    rental = booked.json()["rental"]

    response = _set(owner, s, _week([{"from": "09:00", "to": "18:00"}]))
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["location"]["schedule"]["version"] == 2
    conflicts = body["conflicts"]
    assert [(c["object_id"], c["role"]) for c in conflicts] == [(rental["id"], "issue")]
    assert conflicts[0]["context_type"] == "rental"
    # The booking itself is not changed (§ 22.1.6).
    again = Rental.objects.get(pk=rental["id"])
    assert again.planned_start == datetime.fromisoformat(_local(3))
    assert again.version == rental["version"]
    entry = AuditEvent.objects.get(action="location.schedule_changed")
    assert entry.before is not None and entry.after is not None
    assert entry.before["weekly"]["mon"] == [{"from": "00:00", "to": "24:00"}]
    assert entry.after["conflicts"] == 1
    # Read again: the same conflict.
    read = owner.get(f"/api/c/{s.cmp}/locations/{s.location}/schedule").json()
    assert len(read["conflicts"]) == 1

    # A stale version is refused.
    stale = _set(owner, s, _week([]), version=1)
    assert stale.status_code == 409, stale.content
    # A date exception opens the night of the issue: no conflict.
    opened = _set(
        owner,
        s,
        _week([{"from": "09:00", "to": "18:00"}]),
        [{"date": _day().isoformat(), "intervals": [{"from": "00:00", "to": "24:00"}]}],
        version=2,
    )
    assert opened.status_code == 200, opened.content
    assert opened.json()["conflicts"] == []


@pytest.mark.parametrize(
    ("weekly", "exceptions", "code"),
    [
        ({"mon": []}, [], "SCHEDULE_WEEK_INVALID"),
        (_week([{"from": "10:00", "to": "09:00"}]), [], "SCHEDULE_INTERVAL_EMPTY"),
        (
            _week([{"from": "09:00", "to": "12:00"}, {"from": "11:00", "to": "13:00"}]),
            [],
            "SCHEDULE_INTERVALS_OVERLAP",
        ),
        (_week([{"from": "9", "to": "12:00"}]), [], "SCHEDULE_TIME_INVALID"),
        (
            _week([]),
            [{"date": "2026-12-01", "intervals": []}, {"date": "2026-12-01", "intervals": []}],
            "SCHEDULE_DATE_REPEATED",
        ),
        (_week([]), [{"date": "nope", "intervals": []}], "DATE_INVALID"),
    ],
)
def test_schedule_format_is_validated(
    owner: Client, s: Scenario, weekly: Any, exceptions: Any, code: str
) -> None:
    response = _set(owner, s, weekly, exceptions)
    assert response.status_code == 400, response.content
    assert code in response.content.decode()


def test_context_schedule_overrides_and_restores_inheritance(owner: Client, s: Scenario) -> None:
    from tests.service.test_service import _op, _work

    work = _work(owner, s, name="Oil")
    op = _op(owner, s, [work["id"]], planned_start=_local(10), planned_end=_local(12))
    service = _context(_location(owner, s), "service")
    assert service["schedule"] == {"source": "location", "version": 0, "own": None}
    path = f"/api/c/{s.cmp}/locations/{s.location}/contexts/{service['id']}/schedule"

    response = _put(
        owner,
        path,
        {
            "weekly": _week([{"from": "13:00", "to": "18:00"}]),
            "exceptions": [],
            "expected_version": 0,
        },
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert _context(body["location"], "service")["schedule"]["source"] == "own"
    assert sorted(c["role"] for c in body["conflicts"]) == ["end", "start"]
    assert {c["object_id"] for c in body["conflicts"]} == {op["id"]}
    # The rental context still inherits 24/7; a change of the location does not touch the
    # service context with its own schedule.
    assert _context(body["location"], "rental")["schedule"]["source"] == "location"
    changed = _set(owner, s, _week([{"from": "00:00", "to": "09:00"}]), version=1)
    assert changed.status_code == 200, changed.content
    assert changed.json()["conflicts"] == []

    # «Restore inheritance»: the location's schedule (00:00–09:00) applies again (§ 4.2.4).
    restored = owner.delete(
        path, data={"expected_version": 1}, content_type="application/json", headers=key()
    )
    assert restored.status_code == 200, restored.content
    assert _context(restored.json()["location"], "service")["schedule"]["source"] == "location"
    assert len(restored.json()["conflicts"]) == 2
    assert AuditEvent.objects.filter(action="location.context_schedule_inherited").exists()
    twice = owner.delete(
        path, data={"expected_version": 0}, content_type="application/json", headers=key()
    )
    assert twice.status_code == 422, twice.content
    assert twice.json()["error"]["code"] == "SCHEDULE_ALREADY_INHERITED"


def test_schedule_needs_the_right_to_edit_the_location(owner: Client, s: Scenario) -> None:
    rights = [{"type": "grant", "permission": "vehicle.view"}]
    viewer, _ = hire(owner, s.cmp, "viewer@example.com", rights)
    response = _set(viewer, s, _week([]))
    assert response.status_code == 403, response.content
    assert viewer.get(f"/api/c/{s.cmp}/locations/{s.location}/schedule").status_code == 403


def test_open_moments_follow_the_local_day_and_exceptions() -> None:
    from c4rsys.companies.schedules import Schedule

    day = date(2026, 12, 7)  # a Monday
    weekly: dict[str, list[dict[str, str]]] = {d: [] for d in WEEKDAYS}
    weekly["mon"] = [{"from": "18:00", "to": "24:00"}]
    schedule = Schedule(weekly, [{"date": "2026-12-14", "intervals": []}])
    at = datetime.combine(day, time(23, 30), tzinfo=BKK)
    assert schedule.is_open(at, "Asia/Bangkok")
    # Midnight after a day open until 24:00 is still inside; the next hour is not.
    assert schedule.is_open(at + timedelta(minutes=30), "Asia/Bangkok")
    assert not schedule.is_open(at + timedelta(hours=1), "Asia/Bangkok")
    # The same moment in UTC is read in the local time of the location.
    assert schedule.is_open(at.astimezone(ZoneInfo("UTC")), "Asia/Bangkok")
    # A closed date overrides the weekday.
    assert not schedule.is_open(at + timedelta(days=7), "Asia/Bangkok")
