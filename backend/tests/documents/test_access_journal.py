"""The journal of access to documents and files (ТЗ § 24.4.1) and the security events of attempts
to open an object of another company (§ 2.8.1)."""

import uuid
from typing import Any

import pytest
from django.test import Client

from c4rsys.companies.models import Employment
from c4rsys.platform.models import AuditEvent
from tests.companies.helpers import create_company, key, named_user
from tests.documents.test_registry import _create, _ok
from tests.rentals.scenario import Scenario

pytestmark = pytest.mark.django_db


def test_views_and_downloads_of_a_document_are_journaled(owner: Client, s: Scenario) -> None:
    d = _ok(_create(owner, s), 201)
    employment = Employment.objects.get(
        company__public_id=s.cmp, user__contacts__value="owner@example.com"
    )
    assert owner.get(f"/api/c/{s.cmp}/documents/{d['id']}").status_code == 200
    viewed = AuditEvent.objects.get(action="document.viewed")
    assert viewed.object_id == d["id"]
    assert viewed.company_id == employment.company_id
    assert viewed.actor_employment_id == employment.id

    file_id = d["current_version"]["file_id"]
    assert owner.get(f"/api/files/{file_id}").status_code == 200
    downloaded = AuditEvent.objects.get(action="document.file_downloaded")
    assert downloaded.object_id == d["id"] and downloaded.actor_employment_id == employment.id
    assert (downloaded.after or {}).get("version") == 1
    # Grants of access and new versions are journaled by their commands.
    assert AuditEvent.objects.filter(action="document.created").exists()


def test_a_byte_range_of_the_same_reading_is_not_journaled_twice(
    owner: Client, s: Scenario
) -> None:
    d = _ok(_create(owner, s), 201)
    file_id = d["current_version"]["file_id"]
    owner.get(f"/api/files/{file_id}", headers={"Range": "bytes=0-9"})
    owner.get(f"/api/files/{file_id}", headers={"Range": "bytes=10-19"})
    assert AuditEvent.objects.filter(action="document.file_downloaded").count() == 1


def _stranger() -> tuple[Client, str]:
    client = Client()
    named_user(client, "stranger@example.com")
    response = create_company(client, key()["Idempotency-Key"], display_name="Stranger Rent")
    cmp: str = response.json()["company"]["public_id"]
    return client, cmp


def test_opening_an_object_of_another_company_is_a_security_event(
    owner: Client, s: Scenario
) -> None:
    d = _ok(_create(owner, s), 201)
    stranger, own = _stranger()
    response = stranger.get(f"/api/c/{own}/documents/{d['id']}")
    missing = stranger.get(f"/api/c/{own}/documents/{uuid.uuid4()}")
    # The same answer as for a missing object: the existence is not revealed.
    assert response.status_code == missing.status_code == 404
    assert response.json()["error"]["code"] == missing.json()["error"]["code"]
    events = AuditEvent.objects.filter(action="security.object_not_available").order_by(
        "occurred_at"
    )
    assert [e.object_id for e in events][0] == d["id"]
    event: Any = events[0]
    employment = Employment.objects.get(company__public_id=own)
    assert event.company_id == employment.company_id
    assert event.actor_employment_id == employment.id
    assert event.result == "denied"
    assert "documents" in event.after["route"]
    # Nothing of the document is written into the stranger's journal.
    assert "title" not in str(event.after)


def test_a_company_of_another_user_is_journaled_as_denied(owner: Client, s: Scenario) -> None:
    stranger, _ = _stranger()
    response = stranger.get(f"/api/c/{s.cmp}/documents/{uuid.uuid4()}")
    assert response.status_code == 404
    assert AuditEvent.objects.filter(action="security.company_access_denied").exists()
    # The object event is not doubled for a company the user does not work in.
    assert not AuditEvent.objects.filter(action="security.object_not_available").exists()
