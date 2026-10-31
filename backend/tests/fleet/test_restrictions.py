"""Manual restriction of a vehicle (ТЗ § 8.8.1–8.8.7, § 8.7.2, § 23.5.5–23.5.6)."""

import time
from datetime import timedelta
from typing import Any

import pytest
from django.test import Client
from django.utils import timezone

from c4rsys.fleet import restrictions
from c4rsys.fleet.models import VehicleRestriction
from c4rsys.notifications.models import Notification
from c4rsys.platform.models import AuditEvent
from c4rsys.tasks.models import Task
from tests.accounts.helpers import post
from tests.companies.helpers import hire, key
from tests.rentals.scenario import Scenario, add_location, build, issue

pytestmark = pytest.mark.django_db

GRANT = {"type": "grant", "locations": "all", "contexts": "all", "cash_accounts": "all"}


@pytest.fixture
def s(owner: Client) -> Scenario:
    return build(owner)


def _url(s: Scenario) -> str:
    return f"/api/c/{s.cmp}/vehicles/{s.vehicle}"


def _set(client: Client, s: Scenario, **body: Any) -> Any:
    data: dict[str, Any] = {"reason": "technical_fault", "forbid_rental_issue": True, **body}
    return post(client, f"{_url(s)}/restrictions", data, key())


def _current(client: Client, s: Scenario) -> dict[str, Any]:
    current: dict[str, Any] = client.get(_url(s)).json()["vehicle"]["restriction"]
    return current


def test_set_change_and_lift_one_at_a_time(owner: Client, s: Scenario) -> None:
    ends = timezone.now() + timedelta(days=2)
    created = _set(owner, s, comment="brakes", ends_at=ends.isoformat())
    assert created.status_code == 201, created.content
    current = created.json()["vehicle"]["restriction"]
    assert (current["reason"], current["comment"], current["state"]) == (
        "technical_fault",
        "brakes",
        "active",
    )
    # § 8.8.5: one at a time.
    assert _set(owner, s).json()["error"]["code"] == "VEHICLE_RESTRICTION_EXISTS"
    # The reason, the comment and the term change.
    changed = owner.put(
        f"{_url(s)}/restrictions/{current['id']}",
        data={
            "reason": "safety",
            "comment": "airbag",
            "forbid_rental_issue": True,
            "forbid_moves": True,
            "ends_at": None,
            "expected_version": current["version"],
        },
        content_type="application/json",
        headers=key(),
    )
    assert changed.status_code == 200, changed.content
    current = changed.json()["vehicle"]["restriction"]
    assert (current["reason"], current["ends_at"], current["forbid_moves"]) == (
        "safety",
        None,
        True,
    )
    # Lifted by hand, also without an end.
    lifted = post(
        owner,
        f"{_url(s)}/restrictions/{current['id']}/lift",
        {"expected_version": current["version"], "comment": "fixed"},
        key(),
    )
    assert lifted.status_code == 200, lifted.content
    assert lifted.json()["vehicle"]["restriction"] is None
    actions = list(
        AuditEvent.objects.filter(action__startswith="vehicle.restriction").values_list(
            "action", flat=True
        )
    )
    assert actions == [
        "vehicle.restriction_set",
        "vehicle.restriction_changed",
        "vehicle.restriction_lifted",
    ]
    # After the lift a new one may be set.
    assert _set(owner, s).status_code == 201


def test_reason_from_list_or_own_and_something_forbidden(owner: Client, s: Scenario) -> None:
    assert _set(owner, s, reason="broken").json()["error"]["code"] == "CHOICE_REQUIRED"
    nothing = _set(owner, s, forbid_rental_issue=False)
    assert nothing.json()["error"]["code"] == "RESTRICTION_NOTHING_FORBIDDEN"
    past = _set(owner, s, starts_at=(timezone.now() - timedelta(days=1)).isoformat())
    assert past.json()["error"]["code"] == "RESTRICTION_START_IN_PAST"
    reasons_url = f"/api/c/{s.cmp}/vehicle-restriction-reasons"
    added = post(owner, reasons_url, {"name": "Waiting for parts"}, key())
    assert added.status_code == 201, added.content
    body = added.json()
    assert set(body["system"]) == {
        "technical_fault",
        "safety",
        "document_missing",
        "accident_damage",
        "legal",
        "other",
    }
    custom = body["company"][0]["id"]
    created = _set(owner, s, reason="custom", custom_reason_id=custom)
    assert created.json()["vehicle"]["restriction"]["custom_reason"]["name"] == "Waiting for parts"


def test_issue_blocked_while_it_lasts_and_booking_shows_the_conflict(
    owner: Client, s: Scenario
) -> None:
    now = timezone.now()
    ends = now + timedelta(days=3)
    assert _set(owner, s, ends_at=ends.isoformat()).status_code == 201
    refused = issue(owner, s, actual_start=timezone.now().isoformat())
    assert refused.json()["error"]["code"] == "VEHICLE_RESTRICTED"
    # A future booking is allowed; overlapping the restriction it shows the conflict (§ 8.8.7).
    booked = post(
        owner,
        f"/api/c/{s.cmp}/bookings",
        {
            "vehicle_card_id": s.vehicle,
            "planned_start": (now + timedelta(days=2)).isoformat(),
            "planned_end": (now + timedelta(days=5)).isoformat(),
        },
        key(),
    )
    assert booked.status_code == 201, booked.content
    assert booked.json()["rental"]["booking"]["restriction_conflict"]["ends_at"] is not None
    later = post(
        owner,
        f"/api/c/{s.cmp}/bookings",
        {
            "vehicle_card_id": s.vehicle,
            "planned_start": (now + timedelta(days=6)).isoformat(),
            "planned_end": (now + timedelta(days=7)).isoformat(),
        },
        key(),
    ).json()["rental"]
    assert later["booking"]["restriction_conflict"] is None


def test_moves_refused_and_the_service_exception_needs_its_right(
    owner: Client, s: Scenario
) -> None:
    krabi = add_location(owner, s.cmp, "Krabi")
    service = add_location(owner, s.cmp, "Garage")
    post(owner, f"/api/c/{s.cmp}/locations/{service}/contexts", {"type": "service"}, key())
    assert _set(owner, s, forbid_rental_issue=False, forbid_moves=True).status_code == 201
    driver = owner.get(f"/api/c/{s.cmp}").json()["company"]["employment"]["id"]
    time.sleep(0.2)

    def move(client: Client, to: str, **extra: Any) -> Any:
        # Moves are recorded after they happened, within the restriction.
        body = {
            "to_location_id": to,
            "driver_employment_id": driver,
            "actual_start": (timezone.now() - timedelta(milliseconds=100)).isoformat(),
            "actual_end": (timezone.now() - timedelta(milliseconds=10)).isoformat(),
            **extra,
        }
        return post(client, f"{_url(s)}/move", body, key())

    assert move(owner, krabi).json()["error"]["code"] == "VEHICLE_RESTRICTED"
    # The same for a move entered as a completed trip (decision Д-1).
    recorded = post(
        owner,
        f"/api/c/{s.cmp}/deliveries/record",
        {
            "vehicle_card_id": s.vehicle,
            "to": {"kind": "location", "location_id": krabi},
            "driver_id": driver,
            "actual_start": (timezone.now() - timedelta(milliseconds=100)).isoformat(),
            "actual_end": (timezone.now() - timedelta(milliseconds=10)).isoformat(),
            "result": "accepted_in_location",
        },
        key(),
    )
    assert recorded.json()["error"]["code"] == "VEHICLE_RESTRICTED"
    not_service = move(owner, krabi, restriction_exception_reason="repair")
    assert not_service.json()["error"]["code"] == "MOVE_EXCEPTION_NOT_SERVICE"
    mover, _ = hire(
        owner,
        s.cmp,
        "mover@example.com",
        [{**GRANT, "permission": p} for p in ("vehicle.view", "vehicle.move")],
    )
    assert move(mover, service, restriction_exception_reason="repair").status_code == 403
    allowed = move(owner, service, restriction_exception_reason="repair")
    assert allowed.status_code == 201, allowed.content
    moved = AuditEvent.objects.get(action="vehicle.moved")
    assert (moved.after or {})["restriction_exception"] == "repair"


def test_three_separate_rights(owner: Client, s: Scenario) -> None:
    viewer, _ = hire(owner, s.cmp, "viewer@example.com", [{**GRANT, "permission": "vehicle.view"}])
    assert _set(viewer, s).status_code == 403
    setter, _ = hire(
        owner,
        s.cmp,
        "setter@example.com",
        [{**GRANT, "permission": p} for p in ("vehicle.view", "vehicle.restriction.set")],
    )
    created = _set(setter, s)
    assert created.status_code == 201, created.content
    current = created.json()["vehicle"]["restriction"]
    lift = post(
        setter,
        f"{_url(s)}/restrictions/{current['id']}/lift",
        {"expected_version": current["version"]},
        key(),
    )
    assert lift.status_code == 403


def test_running_rental_is_told_and_the_end_comes_by_itself(owner: Client, s: Scenario) -> None:
    issued = issue(owner, s, actual_start=(timezone.now() - timedelta(days=1)).isoformat())
    assert issued.status_code == 201, issued.content
    created = _set(owner, s, ends_at=(timezone.now() + timedelta(hours=1)).isoformat())
    assert created.status_code == 201, created.content
    # § 8.8.7: the rental goes on; the responsible gets a notification and a task.
    note = Notification.objects.get(event=restrictions.EVENT)
    assert note.params["operation"] == "rental"
    assert Task.objects.filter(system_event=restrictions.EVENT).count() == 1
    # § 8.8.3: at the end it ends by itself, without an employee, and the end is journaled.
    r = VehicleRestriction.objects.get()
    later = timezone.now() + timedelta(hours=2)
    assert restrictions.state_of(r, later) == "ended"
    assert restrictions.run(later) == 1
    entry = AuditEvent.objects.get(action="vehicle.restriction_ended")
    assert entry.actor_user_id is None
    assert restrictions.run(later) == 0
