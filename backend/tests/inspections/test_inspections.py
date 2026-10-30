"""Inspections as records: states, immutability, rights, templates, policy (ТЗ § 10.1–10.3,
§ 10.6, § 23.4.1–23.4.2)."""

from datetime import datetime
from typing import Any

import pytest
from django.db import DatabaseError, transaction
from django.test import Client

from c4rsys.fleet.models import OdometerReading
from c4rsys.inspections.models import Inspection, InspectionTemplateVersion
from c4rsys.notifications.models import Notification
from c4rsys.rentals import notices
from tests.accounts.helpers import post
from tests.companies.helpers import hire, key
from tests.inspections.helpers import act, at, change, code, create, created, ok, photo
from tests.rentals.scenario import NOW, Scenario, issue

pytestmark = pytest.mark.django_db


def _moment(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _templates(client: Client, s: Scenario) -> dict[str, dict[str, Any]]:
    items = client.get(f"/api/c/{s.cmp}/inspection-templates").json()["templates"]
    return {t["transport"]: t for t in items}


def _put_template(client: Client, s: Scenario, template: dict[str, Any], positions: Any) -> Any:
    return client.put(
        f"/api/c/{s.cmp}/inspection-templates/{template['id']}",
        data={"positions": positions, "expected_version": template["version"]},
        content_type="application/json",
        headers=key(),
    )


def test_ready_templates_and_versions(owner: Client, s: Scenario) -> None:
    templates = _templates(owner, s)
    car, moto = templates["car"], templates["motorcycle"]
    car_codes = [p["code"] for p in car["positions"]]
    assert car_codes == [
        "front",
        "rear",
        "left",
        "right",
        "under",
        "top",
        "interior_front",
        "interior_rear",
        "odometer",
    ]
    # § 10.3.5: applicability depends on the transport.
    assert "interior_front" not in [p["code"] for p in moto["positions"]]
    assert all(p["enabled"] and not p["required"] for p in car["positions"])

    first = created(owner, s)
    assert first["template"]["number"] == 1

    positions = [{**p, "required": p["code"] == "front", "count": 2} for p in car["positions"]]
    positions.append(
        {"code": "trunk", "name": "Trunk", "enabled": True, "required": False, "count": 1}
    )
    changed = _put_template(owner, s, car, positions)
    assert changed.status_code == 200, changed.content
    assert changed.json()["template"]["version"] == 2
    stale = _put_template(owner, s, car, positions)
    assert stale.status_code == 409
    not_applicable = _put_template(
        owner, s, moto, [{"code": "interior_front", "enabled": True, "required": False, "count": 1}]
    )
    assert code(not_applicable) == "POSITION_NOT_APPLICABLE"

    second = created(owner, s)
    assert second["template"]["number"] == 2
    # The earlier inspection keeps the version it used (§ 10.3.5).
    again = owner.get(f"/api/c/{s.cmp}/inspections/{first['id']}").json()["inspection"]
    assert again["template"]["number"] == 1
    with pytest.raises(DatabaseError), transaction.atomic():
        InspectionTemplateVersion.objects.filter(number=1).update(positions=[])


def test_states_and_immutability(owner: Client, s: Scenario) -> None:
    item = created(
        owner,
        s,
        odometer="10100",
        fuel="6",
        participant_kind="other",
        participant_text="Owner's driver",
    )
    assert (item["state"], _moment(item["performed_at"])) == ("draft", NOW)
    assert item["performer_employment_id"] and item["author_user_id"]

    future = change(owner, s, item, performed_at=at(1), odometer="10100")
    assert code(future) == "FUTURE_TIME_NOT_ALLOWED"
    item = ok(change(owner, s, item, performed_at=at(-2), odometer="10150", fuel="6"))
    assert _moment(item["performed_at"]) == _moment(at(-2))

    item = ok(act(owner, s, item, "complete"))
    assert (item["state"], item["check_status"]) == ("saved", "not_required")
    reading = OdometerReading.objects.get(source_id=item["id"])
    assert (reading.source_type, str(reading.value)) == ("inspection", "10150.0")

    assert code(change(owner, s, item, odometer="1")) == "INSPECTION_NOT_EDITABLE"
    assert code(act(owner, s, item, "terminate", reason="x")) == "INSPECTION_NOT_STARTED"
    with pytest.raises(DatabaseError), transaction.atomic():
        Inspection.objects.filter(pk=item["id"]).update(fuel_value=1)
    deleted = owner.delete(
        f"/api/c/{s.cmp}/inspections/{item['id']}",
        data={"expected_version": item["version"]},
        content_type="application/json",
        headers=key(),
    )
    assert code(deleted) == "INSPECTION_NOT_DRAFT"

    assert act(owner, s, item, "invalidate", reason="").status_code == 400
    invalid = ok(act(owner, s, item, "invalidate", reason="Wrong vehicle"))
    assert (invalid["state"], invalid["invalidated_reason"]) == ("invalid", "Wrong vehicle")
    with pytest.raises(DatabaseError), transaction.atomic():
        Inspection.objects.filter(pk=item["id"]).update(invalidated_reason="other")


def test_planned_cancelled_terminated_and_deleted(owner: Client, s: Scenario) -> None:
    planned = created(owner, s, planned_at=at(5))
    assert planned["state"] == "planned"
    assert code(act(owner, s, planned, "complete")) == "INSPECTION_NOT_DRAFT"
    assert act(owner, s, planned, "cancel").status_code == 400
    cancelled = ok(act(owner, s, planned, "cancel", reason="Client did not come"))
    assert (cancelled["state"], cancelled["end_reason"]) == ("cancelled", "Client did not come")
    assert code(act(owner, s, cancelled, "start")) == "INSPECTION_NOT_PLANNED"

    other = created(owner, s, planned_at=at(5))
    started = ok(act(owner, s, other, "start"))
    assert (started["state"], _moment(started["performed_at"])) == ("in_progress", NOW)
    started = ok(change(owner, s, started, odometer="10200", fuel="3"))
    terminated = ok(act(owner, s, started, "terminate", reason="Vehicle taken away"))
    # The data stay, but are not a result (§ 10.6.3).
    assert (terminated["state"], terminated["odometer"]) == ("terminated", "10200.0")
    assert not OdometerReading.objects.filter(source_id=terminated["id"]).exists()

    draft = created(owner, s)
    removed = owner.delete(
        f"/api/c/{s.cmp}/inspections/{draft['id']}",
        data={"expected_version": draft["version"]},
        content_type="application/json",
        headers=key(),
    )
    assert removed.status_code == 200, removed.content
    assert not Inspection.objects.filter(pk=draft["id"]).exists()


def test_required_photos_only_for_a_conducted_check(owner: Client, s: Scenario) -> None:
    car = _templates(owner, s)["car"]
    positions = [{**p, "required": p["code"] == "front"} for p in car["positions"]]
    assert _put_template(owner, s, car, positions).status_code == 200
    item = created(owner, s)

    missing = act(owner, s, item, "complete", conducted=True)
    assert code(missing) == "INSPECTION_REQUIRED_MISSING"
    assert missing.json()["error"]["params"]["missing"] == ["photo:front"]
    # Without the check the missing photos do not prevent the save (§ 10.3.4).
    other = created(owner, s)
    assert ok(act(owner, s, other, "complete"))["check_status"] == "not_required"

    file_id = photo(owner)
    unknown = change(owner, s, item, files=[{"file_id": file_id, "kind": "photo", "position": "x"}])
    assert code(unknown) == "POSITION_UNKNOWN"
    item = ok(
        change(owner, s, item, files=[{"file_id": file_id, "kind": "photo", "position": "front"}])
    )
    saved = ok(act(owner, s, item, "complete", conducted=True))
    assert saved["check_status"] == "conducted"
    assert saved["files"] == [
        {"file_id": file_id, "kind": "photo", "position": "front", "state": "ready"}
    ]
    # The photo is read by employees who see the inspection.
    assert owner.get(f"/api/files/{file_id}").status_code == 200


def test_policy_by_kind_and_application(owner: Client, s: Scenario) -> None:
    body = {
        "kind": "out_of_rental",
        "application": "standalone",
        "check_required": True,
        "odometer_required": True,
        "fuel_required": False,
        "cleanliness_required": True,
    }
    response = owner.put(
        f"/api/c/{s.cmp}/inspection-policies",
        data=body,
        content_type="application/json",
        headers=key(),
    )
    assert response.status_code == 200, response.content
    rental_issue = next(
        p
        for p in response.json()["policies"]
        if (p["kind"], p["application"]) == ("issue", "rental")
    )
    # The check of a rental issue is the issue policy's (§ 14.3.6); readings always required.
    assert rental_issue["check_required"] is None and rental_issue["odometer_required"] is True

    item = created(owner, s)
    missing = act(owner, s, item, "complete")
    assert missing.json()["error"]["params"]["missing"] == ["odometer", "cleanliness"]
    item = ok(change(owner, s, item, odometer="10000", cleanliness="Clean"))
    saved = ok(act(owner, s, item, "complete"))
    assert (saved["check_required"], saved["check_status"]) == (True, "required_not_done")


def test_rights_of_inspections(owner: Client, s: Scenario) -> None:
    mechanic, _ = hire(owner, s.cmp, "m@example.com", [{"type": "set", "set": "mechanic"}])
    manager, _ = hire(owner, s.cmp, "r@example.com", [{"type": "set", "set": "rental_manager"}])
    driver, _ = hire(owner, s.cmp, "d@example.com", [{"type": "set", "set": "driver"}])

    item = created(mechanic, s, odometer="10000")
    saved = ok(act(mechanic, s, item, "complete", conducted=True))
    assert saved["performer_employment_id"] != created(owner, s)["performer_employment_id"]
    # § 23.2.4: the mechanic does not recognise a saved inspection invalid.
    assert act(mechanic, s, saved, "invalidate", reason="x").status_code == 403
    planned = created(manager, s, planned_at=at(3))
    assert act(manager, s, planned, "cancel", reason="x").status_code == 403
    assert ok(act(owner, s, planned, "cancel", reason="x"))["state"] == "cancelled"

    assert create(driver, s).status_code == 403
    assert driver.get(f"/api/c/{s.cmp}/inspections/{saved['id']}").status_code == 404
    assert driver.get(f"/api/c/{s.cmp}/inspections").json()["inspections"] == []
    history = manager.get(f"/api/c/{s.cmp}/vehicles/{s.vehicle}/inspections").json()
    assert [i["id"] for i in history["inspections"]] == [saved["id"]]
    policy = {
        "kind": "out_of_rental",
        "application": "service",
        "check_required": True,
        "odometer_required": False,
        "fuel_required": False,
        "cleanliness_required": False,
    }
    refused = manager.put(
        f"/api/c/{s.cmp}/inspection-policies",
        data=policy,
        content_type="application/json",
        headers=key(),
    )
    assert refused.status_code == 403


def _policy_required(owner: Client, s: Scenario) -> None:
    body = {"debt": "warn", "payment": "warn", "documents": "none", "inspection": "required"}
    response = owner.put(
        f"/api/c/{s.cmp}/issue-policy", data=body, content_type="application/json", headers=key()
    )
    assert response.status_code == 200, response.content


def test_required_check_is_a_conflict_and_closing_needs_a_reason(
    owner: Client, s: Scenario
) -> None:
    _policy_required(owner, s)
    issued = issue(owner, s)
    # § 10.3.3: not a refusal.
    assert issued.status_code == 201, issued.content
    rental = issued.json()["rental"]
    assert rental["inspections"]["check_conflicts"] == ["issue"]
    notices.run(NOW)
    assert Notification.objects.filter(event="rental.inspection_check_missing").exists()

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
    assert returned.status_code == 200, returned.content  # the return is not blocked
    rental = returned.json()["rental"]
    assert rental["formalization"]["missing"] == [
        "issue_inspection_check_missing",
        "return_inspection_check_missing",
    ]
    url = f"/api/c/{s.cmp}/rentals/{rental['id']}/inspection-reasons"
    refused = post(
        owner,
        f"/api/c/{s.cmp}/rentals/{rental['id']}/complete-formalization",
        {"expected_version": rental["version"]},
        key(),
    )
    assert code(refused) == "FORMALIZATION_INCOMPLETE"

    # A conducted inspection of the return made through the form settles it.
    form = created(
        owner,
        s,
        kind="return",
        application="rental",
        rental_id=rental["id"],
        odometer="10300",
        fuel="8",
    )
    ok(act(owner, s, form, "complete", conducted=True))
    recorded = post(owner, url, {"kind": "issue", "reason": "Client was in a hurry"}, key())
    assert recorded.status_code == 200, recorded.content
    assert recorded.json()["conflicts"] == []
    assert code(post(owner, url, {"kind": "issue", "reason": "again"}, key())) == (
        "INSPECTION_NOT_MISSING"
    )
    detail = owner.get(f"/api/c/{s.cmp}/rentals/{rental['id']}").json()["rental"]
    assert detail["formalization"]["missing"] == []
    notices.run(NOW)
    assert not Notification.objects.filter(
        event="rental.inspection_check_missing", state=Notification.State.ACTIVE
    ).exists()
    done = post(
        owner,
        f"/api/c/{s.cmp}/rentals/{rental['id']}/complete-formalization",
        {"expected_version": detail["version"]},
        key(),
    )
    assert done.status_code == 200, done.content


def test_invalidated_conducted_check_is_missing_again(owner: Client, s: Scenario) -> None:
    _policy_required(owner, s)
    rental = issue(owner, s, inspection_conducted=True).json()["rental"]
    assert rental["inspections"]["check_conflicts"] == []
    inspection = owner.get(f"/api/c/{s.cmp}/inspections?rental_id={rental['id']}").json()[
        "inspections"
    ][0]
    ok(act(owner, s, inspection, "invalidate", reason="Readings of another car"))
    # § 10.6.3: an invalid required inspection counts as not done.
    detail = owner.get(f"/api/c/{s.cmp}/rentals/{rental['id']}").json()["rental"]
    assert detail["inspections"]["check_conflicts"] == ["issue"]


def test_history_of_an_inspection_and_its_files_state(owner: Client, s: Scenario) -> None:
    """The saved inspection shows its journal with its damages (§ 10.6.3) and the state of its
    files (a video is compressed after the upload, decision В-8.1)."""
    file_id = photo(owner)
    item = created(owner, s, files=[{"file_id": file_id, "kind": "photo", "position": "front"}])
    assert item["files"] == [
        {"file_id": file_id, "kind": "photo", "position": "front", "state": "ready"}
    ]
    entry = {"zone_text": "Front bumper", "photo_file_ids": [file_id]}
    item = ok(
        change(owner, s, item, odometer="100", fuel="10", damages=[entry], files=item["files"])
    )
    ok(act(owner, s, item, "complete", conducted=True))

    history = owner.get(f"/api/c/{s.cmp}/inspections/{item['id']}/history").json()["history"]

    actions = [h["action"] for h in history]
    assert actions[0] == "inspection.draft_created"
    assert "inspection.saved" in actions and "damage.created" in actions
