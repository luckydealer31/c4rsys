"""Documents, part Р4: documents required at the stages of a rental (ТЗ § 18.4.1; customer's
decision Р-8 of 29.10.2026)."""

from collections.abc import Iterator
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from django.test import Client

from c4rsys.rentals import issue as issue_module
from c4rsys.rentals import returns as returns_module
from tests.accounts.helpers import post
from tests.companies.helpers import key
from tests.documents.test_registry import _create, _file, _kind
from tests.rentals.scenario import NOW, Scenario, add_vehicle, issue

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def frozen() -> Iterator[None]:
    clock = SimpleNamespace(now=lambda: NOW)
    with (
        mock.patch.object(issue_module, "timezone", clock),
        mock.patch.object(returns_module, "timezone", clock),
    ):
        yield


def _require(client: Client, s: Scenario, **body: Any) -> Any:
    return post(client, f"/api/c/{s.cmp}/document-requirements", body, key())


def _policy(client: Client, s: Scenario, documents: str) -> None:
    body = {"debt": "warn", "payment": "warn", "documents": documents, "inspection": "not_required"}
    client.put(
        f"/api/c/{s.cmp}/issue-policy",
        data=body,
        content_type="application/json",
        headers=key(),
    )


def _book(client: Client, s: Scenario, vehicle: str) -> dict[str, Any]:
    start = NOW - timedelta(hours=1)
    response = post(
        client,
        f"/api/c/{s.cmp}/bookings",
        {
            "vehicle_card_id": vehicle,
            "planned_start": start.isoformat(),
            "planned_end": (start + timedelta(days=1)).isoformat(),
            "client_id": s.client,
            "tariff_id": s.tariff,
            "issue_location_id": s.location,
        },
        key(),
    )
    assert response.status_code == 201, response.content
    booking: dict[str, Any] = response.json()["rental"]
    return booking


def test_document_before_issue_by_the_policy(owner: Client, s: Scenario) -> None:
    templates = owner.get(f"/api/c/{s.cmp}/document-templates").json()["templates"]
    contract_ru = next(
        t for t in templates if t["code"] == "rental_contract" and t["language"] == "ru"
    )
    contract_en = next(
        t for t in templates if t["code"] == "rental_contract" and t["language"] == "en"
    )
    added = _require(owner, s, stage="before_issue", need="present", template_id=contract_ru["id"])
    assert added.status_code == 201, added.content
    again = _require(owner, s, stage="before_issue", need="present", template_id=contract_ru["id"])
    assert again.json()["error"]["code"] == "DOCUMENT_REQUIREMENT_EXISTS"

    # Documents are not required by the policy: the issue warns only.
    rental = issue(owner, s)
    assert rental.status_code == 201, rental.content
    assert rental.json()["warnings"][0]["code"] == "DOCUMENTS_MISSING"

    # Required: a booking without the contract needs an exception; with it the issue goes.
    _policy(owner, s, "require")
    other = add_vehicle(owner, s, s.location, "Honda Jazz")
    booking = _book(owner, s, other)
    later = {
        "actual_start": (NOW - timedelta(hours=1)).isoformat(),
        "booking_id": booking["id"],
        "vehicle_card_id": other,
    }
    refused = issue(owner, s, **later, odometer="10500")
    assert refused.json()["error"]["code"] == "ISSUE_REQUIREMENTS_UNMET"
    assert refused.json()["error"]["params"]["requirements"][0]["code"] == "documents"
    # A starting template counts in any of its languages.
    made = post(
        owner,
        f"/api/c/{s.cmp}/documents/from-template",
        {"template_id": contract_en["id"], "source_id": booking["id"]},
        key(),
    )
    assert made.status_code == 201, made.content
    issued = issue(owner, s, **later, odometer="10500")
    assert issued.status_code == 201, issued.content
    assert "DOCUMENTS_MISSING" not in {w["code"] for w in issued.json()["warnings"]}


def test_signed_document_at_return_completes_formalization(owner: Client, s: Scenario) -> None:
    act = _kind(owner, s, "act")
    assert _require(owner, s, stage="at_return", need="signed", kind_id=act).status_code == 201
    rental = issue(owner, s).json()["rental"]
    returned = post(
        owner,
        f"/api/c/{s.cmp}/rentals/{rental['id']}/return",
        {
            "actual_end": NOW.isoformat(),
            "return_location_id": s.location,
            "expected_version": rental["version"],
            "odometer": "10300",
            "fuel": "8",
        },
        key(),
    ).json()["rental"]
    assert returned["formalization"]["missing"] == ["return_documents_missing"]

    link = {"object_type": "rental", "object_id": rental["id"]}
    d = _create(owner, s, kind_id=act, links=[link]).json()["document"]
    after_upload = owner.get(f"/api/c/{s.cmp}/rentals/{rental['id']}").json()["rental"]
    assert after_upload["formalization"]["missing"] == ["return_documents_missing"]
    sent = post(
        owner,
        f"/api/c/{s.cmp}/documents/{d['id']}/signing",
        {"expected_version": d["version"], "signers": [{"role": "client", "id": s.client}]},
        key(),
    ).json()["signing"]
    signer = sent["signers"][0]["id"]
    paper = post(
        owner,
        f"/api/c/{s.cmp}/documents/{d['id']}/signers/{signer}/paper",
        {"file_id": _file(owner), "signed_on": "2026-10-01"},
        key(),
    )
    assert paper.status_code == 200, paper.content
    current = owner.get(f"/api/c/{s.cmp}/rentals/{rental['id']}").json()["rental"]
    assert current["formalization"]["missing"] == []
    done = post(
        owner,
        f"/api/c/{s.cmp}/rentals/{rental['id']}/complete-formalization",
        {"expected_version": current["version"]},
        key(),
    )
    assert done.status_code == 200, done.content


def test_requirements_need_settings_right(owner: Client, s: Scenario) -> None:
    from tests.companies.helpers import hire

    viewer, _ = hire(
        owner, s.cmp, "viewer@example.com", [{"type": "grant", "permission": "documents.view"}]
    )
    act = _kind(owner, s, "act")
    assert _require(viewer, s, stage="at_issue", need="present", kind_id=act).status_code == 403
    item = _require(owner, s, stage="at_issue", need="present", kind_id=act).json()["requirement"]
    listed = viewer.get(f"/api/c/{s.cmp}/document-requirements").json()["requirements"]
    assert [x["id"] for x in listed] == [item["id"]]
    url = f"/api/c/{s.cmp}/document-requirements/{item['id']}/remove"
    assert post(owner, url, {}, key()).status_code == 200
    assert owner.get(f"/api/c/{s.cmp}/document-requirements").json()["requirements"] == []
