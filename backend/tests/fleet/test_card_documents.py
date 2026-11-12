"""Documents of a vehicle kept by the company in its card (customer's decision of 12.11.2026):
the name, the series and the number, the start and the end of the term, an optional JPEG or PDF
file; added, changed and sent to the archive."""

from typing import Any

import pytest
from django.test import Client

from c4rsys.platform.models import AuditEvent
from tests.accounts.helpers import post
from tests.companies.helpers import hire, key
from tests.files.helpers import upload
from tests.rentals.scenario import Scenario, build

pytestmark = pytest.mark.django_db

PDF = b"%PDF-1.4\n" + b"0" * 100


@pytest.fixture
def s(owner: Client) -> Scenario:
    return build(owner)


def _url(s: Scenario) -> str:
    return f"/api/c/{s.cmp}/vehicles/{s.vehicle}"


def _documents(client: Client, s: Scenario) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = client.get(_url(s)).json()["vehicle"]["card_documents"]
    return items


def _put(client: Client, url: str, data: dict[str, Any]) -> Any:
    return client.put(url, data=data, content_type="application/json", headers=key())


def test_add_change_and_archive(owner: Client, s: Scenario) -> None:
    # Only the name is required: the dates, the series, the number and the file are optional.
    assert post(owner, f"{_url(s)}/card-documents", {"name": ""}, key()).status_code == 400
    added = post(owner, f"{_url(s)}/card-documents", {"name": "Blue Book"}, key())
    assert added.status_code == 201, added.content
    [d] = added.json()["vehicle"]["card_documents"]
    assert (d["name"], d["number"], d["valid_from"], d["has_file"], d["state"]) == (
        "Blue Book",
        "",
        None,
        False,
        "active",
    )
    # The end of the term is not before its start.
    wrong = _put(
        owner,
        f"{_url(s)}/card-documents/{d['id']}",
        {
            "name": "Blue Book",
            "valid_from": "2026-05-01",
            "valid_until": "2026-04-01",
            "expected_version": d["version"],
        },
    )
    assert wrong.json()["error"]["code"] == "PERIOD_INVALID"
    changed = _put(
        owner,
        f"{_url(s)}/card-documents/{d['id']}",
        {
            "name": "Blue Book",
            "number": "AB 123456",
            "valid_from": "2026-05-01",
            "valid_until": "2027-05-01",
            "expected_version": d["version"],
        },
    )
    assert changed.status_code == 200, changed.content
    [d2] = changed.json()["vehicle"]["card_documents"]
    assert (d2["number"], d2["valid_from"], d2["valid_until"]) == (
        "AB 123456",
        "2026-05-01",
        "2027-05-01",
    )
    # A stale version is refused.
    stale = post(
        owner,
        f"{_url(s)}/card-documents/{d['id']}/archive",
        {"expected_version": d["version"]},
        key(),
    )
    assert stale.status_code == 409
    archived = post(
        owner,
        f"{_url(s)}/card-documents/{d['id']}/archive",
        {"expected_version": d2["version"]},
        key(),
    )
    assert archived.status_code == 200, archived.content
    [d3] = _documents(owner, s)
    assert d3["state"] == "archived"
    # An archived document is not changed.
    again = _put(
        owner,
        f"{_url(s)}/card-documents/{d['id']}",
        {"name": "Other", "expected_version": d3["version"]},
    )
    assert again.json()["error"]["code"] == "VEHICLE_DOCUMENT_ARCHIVED"
    actions = set(
        AuditEvent.objects.filter(action__startswith="vehicle_document.").values_list(
            "action", flat=True
        )
    )
    assert actions == {
        "vehicle_document.added",
        "vehicle_document.changed",
        "vehicle_document.archived",
    }


def test_file_is_jpeg_or_pdf(owner: Client, s: Scenario) -> None:
    png = upload(owner, "vehicle_document", key="f-png").json()["file"]["id"]
    refused = post(owner, f"{_url(s)}/card-documents", {"name": "Policy", "file_id": png}, key())
    assert refused.json()["error"]["code"] == "VEHICLE_DOCUMENT_FILE_TYPE"
    pdf = upload(owner, "vehicle_document", PDF, key="f-pdf").json()["file"]["id"]
    added = post(
        owner,
        f"{_url(s)}/card-documents",
        {"name": "Policy", "file_id": pdf, "file_name": "policy.pdf"},
        key(),
    )
    assert added.status_code == 201, added.content
    [d] = added.json()["vehicle"]["card_documents"]
    assert (d["has_file"], d["file_name"]) == (True, "policy.pdf")
    got = owner.get(f"{_url(s)}/card-documents/{d['id']}/file")
    assert got.status_code == 200
    assert got["Content-Type"] == "application/pdf"
    # A change without a new file keeps the attached one when asked to; otherwise removes it.
    url = f"{_url(s)}/card-documents/{d['id']}"
    kept = _put(
        owner, url, {"name": "Policy 2", "keep_file": True, "expected_version": d["version"]}
    )
    [d2] = kept.json()["vehicle"]["card_documents"]
    assert (d2["name"], d2["has_file"]) == ("Policy 2", True)
    removed = _put(owner, url, {"name": "Policy 2", "expected_version": d2["version"]})
    assert removed.json()["vehicle"]["card_documents"][0]["has_file"] is False


def test_needs_the_right_to_edit_the_vehicle(owner: Client, s: Scenario) -> None:
    grant = {"type": "grant", "locations": "all", "contexts": "all", "cash_accounts": "all"}
    clerk, _ = hire(owner, s.cmp, "clerk@example.com", [{**grant, "permission": "vehicle.view"}])
    assert _documents(clerk, s) == []
    denied = post(clerk, f"{_url(s)}/card-documents", {"name": "Blue Book"}, key())
    assert denied.status_code == 403
