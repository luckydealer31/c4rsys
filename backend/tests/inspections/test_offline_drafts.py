"""Sending a local draft made without connection (ТЗ § 10.4.2–10.4.4): it becomes a usual draft
inspection once, a repeated sending never makes a second record, and possible duplicates are
shown to the employee, who decides."""

import uuid
from typing import Any

import pytest
from django.test import Client

from c4rsys.inspections.models import Inspection
from c4rsys.platform.models import AuditEvent
from tests.companies.helpers import hire
from tests.inspections.helpers import at, code, create, created, photo
from tests.rentals.scenario import Scenario

pytestmark = pytest.mark.django_db


def _offline(**body: Any) -> dict[str, Any]:
    return {"client_draft_id": str(uuid.uuid4()), "performed_at": at(-1), **body}


def test_offline_draft_is_created_once(owner: Client, s: Scenario) -> None:
    file_id = photo(owner, "offline")
    body = _offline(
        odometer="10300",
        fuel="5",
        files=[{"file_id": file_id, "kind": "photo", "position": "front"}],
    )
    item = created(owner, s, **body)
    assert (item["state"], item["client_draft_id"]) == ("draft", body["client_draft_id"])
    assert [f["file_id"] for f in item["files"]] == [file_id]
    event = AuditEvent.objects.get(object_id=item["id"], action="inspection.draft_created")
    assert event.after is not None
    assert event.after["offline_draft"] == body["client_draft_id"]

    # The answer was lost and the device sends the same draft again with a new key.
    again = create(owner, s, **{**body, "files": [], "duplicates_confirmed": True})
    assert again.status_code == 409
    assert code(again) == "OFFLINE_DRAFT_ALREADY_SENT"
    assert again.json()["error"]["params"]["inspection_id"] == item["id"]
    assert Inspection.objects.filter(client_draft_id=body["client_draft_id"]).count() == 1


def test_possible_duplicates_are_resolved_by_the_employee(owner: Client, s: Scenario) -> None:
    existing = created(owner, s, performed_at=at(-2))
    body = _offline()
    refused = create(owner, s, **body)
    assert refused.status_code == 409
    assert code(refused) == "INSPECTION_POSSIBLE_DUPLICATE"
    assert [i["id"] for i in refused.json()["error"]["params"]["inspections"]] == [existing["id"]]
    assert not Inspection.objects.filter(client_draft_id=body["client_draft_id"]).exists()

    # Another kind or a moment far away is not a duplicate.
    assert created(owner, s, **_offline(kind="issue"))["kind"] == "issue"
    assert created(owner, s, **_offline(performed_at=at(-10)))["state"] == "draft"

    confirmed = created(owner, s, **body, duplicates_confirmed=True)
    assert confirmed["client_draft_id"] == body["client_draft_id"]
    event = AuditEvent.objects.get(object_id=confirmed["id"], action="inspection.draft_created")
    assert event.after is not None
    assert event.after["duplicates_confirmed"] is True


def test_offline_draft_rules(owner: Client, s: Scenario) -> None:
    assert code(create(owner, s, **_offline(planned_at=at(5)))) == "FIELD_NOT_APPLICABLE"
    assert code(create(owner, s, **_offline(performed_at=at(1)))) == "FUTURE_TIME_NOT_ALLOWED"
    assert create(owner, s, client_draft_id="not-a-uuid").status_code == 400
    # The right to create inspections is checked as for any inspection (§ 23.4.1).
    driver, _ = hire(owner, s.cmp, "d@example.com", [{"type": "set", "set": "driver"}])
    assert create(driver, s, **_offline()).status_code == 403
    # An ordinary inspection is not checked for duplicates.
    created(owner, s)
    assert created(owner, s)["client_draft_id"] is None
