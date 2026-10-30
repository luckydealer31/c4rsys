"""The act of inspection and the signature on the screen (ТЗ § 10.2.2, § 18.1.8, § 18.4; customer's
decision В-8.3 of 30.10.2026; part О6)."""

from typing import Any

import pytest
from django.test import Client

from c4rsys.documents.models import DocumentSigner
from tests.accounts.helpers import post
from tests.companies.helpers import hire, key
from tests.files.helpers import PNG, upload
from tests.inspections.helpers import act as do
from tests.inspections.helpers import code, created, ok
from tests.rentals.scenario import NOW, Scenario, issue

pytestmark = pytest.mark.django_db


def _issued(client: Client, s: Scenario) -> tuple[dict[str, Any], dict[str, Any]]:
    """A rental and its saved inspection at the issue (§ 10.1.4)."""
    rental = issue(client, s)
    assert rental.status_code == 201, rental.content
    item = rental.json()["rental"]
    found = client.get(f"/api/c/{s.cmp}/inspections?rental_id={item['id']}").json()["inspections"]
    inspection = next(i for i in found if i["kind"] == "issue")
    return item, inspection


def _act(client: Client, s: Scenario, inspection: dict[str, Any], **body: Any) -> Any:
    data = {"language": "en", **body}
    return post(client, f"/api/c/{s.cmp}/inspections/{inspection['id']}/act", data, key())


def _made(client: Client, s: Scenario, inspection: dict[str, Any]) -> dict[str, Any]:
    response = _act(client, s, inspection)
    assert response.status_code == 201, response.content
    found: dict[str, Any] = response.json()["document"]
    return found


def _signing(client: Client, s: Scenario, d: dict[str, Any]) -> dict[str, Any]:
    found: dict[str, Any] = client.get(f"/api/c/{s.cmp}/documents/{d['id']}/signing").json()[
        "signing"
    ]
    return found


def _signature(client: Client, content: bytes = PNG) -> Any:
    return upload(client, "document_signature", content, key=key()["Idempotency-Key"])


def _on_screen(client: Client, s: Scenario, d: dict[str, Any], **body: Any) -> Any:
    signer = _signing(client, s, d)["signers"][0]
    data = {"version_number": signer["version_number"], **body}
    url = f"/api/c/{s.cmp}/documents/{d['id']}/signers/{signer['id']}/on-screen"
    return post(client, url, data, key())


def _sign(client: Client, s: Scenario, d: dict[str, Any]) -> dict[str, Any]:
    file_id = _signature(client).json()["file"]["id"]
    response = _on_screen(client, s, d, decision="sign", consent=True, file_id=file_id)
    assert response.status_code == 200, response.content
    found: dict[str, Any] = response.json()["signing"]
    return found


def test_act_is_a_snapshot_of_the_saved_inspection(owner: Client, s: Scenario) -> None:
    rental, inspection = _issued(owner, s)
    d = _made(owner, s, inspection)
    content = d["versions"][0]["content"]
    assert "Vehicle inspection act No." in content
    assert "At the issue" in content and "10000 km" in content
    assert "<th>Zone</th>" in content
    links = {(x["object_type"], x["object_id"]) for x in d["links"]}
    assert ("inspection", inspection["id"]) in links
    assert ("rental", rental["id"]) in links
    assert ("vehicle", s.vehicle) in links
    assert ("client", s.client) in links
    assert (d["source_type"], d["source_id"]) == ("inspection", inspection["id"])
    kinds = owner.get(f"/api/c/{s.cmp}/document-kinds").json()["kinds"]
    assert next(k for k in kinds if k["id"] == d["kind_id"])["code"] == "inspection_act"

    # Sent at once to the client of the rental (§ 10.2.2).
    signing = _signing(owner, s, d)
    assert signing["on_screen"] is True
    assert [(x["role"], x["client_id"], x["state"]) for x in signing["signers"]] == [
        ("client", s.client, "pending")
    ]
    assert code(_act(owner, s, inspection)) == "INSPECTION_ACT_EXISTS"
    # Made by the act only: the general making by a template is refused.
    templates = owner.get(f"/api/c/{s.cmp}/document-templates").json()["templates"]
    starter = next(t for t in templates if t["code"] == "inspection_act" and t["language"] == "ru")
    generic = post(
        owner,
        f"/api/c/{s.cmp}/documents/from-template",
        {"template_id": starter["id"], "source_id": inspection["id"]},
        key(),
    )
    assert code(generic) == "TEMPLATE_SOURCE_MISMATCH"
    russian = owner.get(f"/api/c/{s.cmp}/documents/{d['id']}").json()["document"]
    assert russian["template_id"] != starter["id"]

    draft = created(owner, s)
    assert code(_act(owner, s, draft)) == "INSPECTION_ACT_NOT_SAVED"


def test_act_of_participant_counterparty_in_russian(owner: Client, s: Scenario) -> None:
    party = post(
        owner,
        f"/api/c/{s.cmp}/counterparties",
        {
            "kind": "company",
            "company_name": "Garage Ltd",
            "contacts": [{"kind": "email", "value": "g@x.example"}],
        },
        key(),
    )
    assert party.status_code == 201, party.content
    party_id = party.json()["counterparty"]["id"]
    item = created(
        owner,
        s,
        participant_kind="counterparty",
        participant_counterparty_id=party_id,
        odometer="12000.5",
        cleanliness="clean",
    )
    saved = ok(do(owner, s, item, "complete", conducted=True))
    response = _act(owner, s, saved, language="ru")
    assert response.status_code == 201, response.content
    d = response.json()["document"]
    content = d["versions"][0]["content"]
    assert "Акт осмотра" in content and "Вне аренды" in content
    assert "12000.5 km" in content and "Garage Ltd" in content
    signers = _signing(owner, s, d)["signers"]
    assert [(x["role"], x["counterparty_id"]) for x in signers] == [("counterparty", party_id)]


def test_signature_on_screen_keeps_image_version_and_presenter(owner: Client, s: Scenario) -> None:
    _, inspection = _issued(owner, s)
    d = _made(owner, s, inspection)
    jpeg = _signature(owner, b"\xff\xd8\xff" + b"\x00" * 64)
    assert code(jpeg) == "FILE_TYPE_NOT_ALLOWED"
    file_id = _signature(owner).json()["file"]["id"]
    refused = _on_screen(owner, s, d, decision="sign", consent=False, file_id=file_id)
    assert code(refused) == "SIGNING_CONSENT_REQUIRED"
    assert code(_on_screen(owner, s, d, decision="sign", consent=True)) == "FIELD_REQUIRED"
    stale = _on_screen(
        owner, s, d, decision="sign", consent=True, file_id=file_id, version_number=9
    )
    assert code(stale) == "SIGNING_VERSION_REPLACED"

    signing = _sign(owner, s, d)
    signer = signing["signers"][0]
    assert signing["status"] == {"state": "signed", "version": 1}
    assert (signer["state"], signer["method"]) == ("signed", "on_screen")
    assert signer["signature_file_id"] and signer["presented_by_me"] is True
    assert signer["entered_at"] and signer["decided_at"]
    row = DocumentSigner.objects.get(pk=signer["id"])
    assert row.presented_session_id is not None and row.entered_by_user_id is not None
    assert row.document_version.number == 1

    # The image is read by the rights on the document (§ 18.5.3).
    viewer, _ = hire(
        owner, s.cmp, "viewer@example.com", [{"type": "grant", "permission": "documents.view"}]
    )
    image = viewer.get(f"/api/files/{signer['signature_file_id']}")
    assert image.status_code == 200 and image["Content-Type"] == "image/png"
    stranger, _ = hire(
        owner, s.cmp, "nobody@example.com", [{"type": "grant", "permission": "rental.view"}]
    )
    assert stranger.get(f"/api/files/{signer['signature_file_id']}").status_code == 404
    again = _on_screen(owner, s, d, decision="sign", consent=True, file_id=file_id)
    assert code(again) == "SIGNER_ALREADY_ANSWERED"


def test_refusal_on_screen_is_kept(owner: Client, s: Scenario) -> None:
    _, inspection = _issued(owner, s)
    d = _made(owner, s, inspection)
    response = _on_screen(owner, s, d, decision="decline", comment="The scratch is not mine")
    assert response.status_code == 200, response.content
    signing = response.json()["signing"]
    signer = signing["signers"][0]
    assert signing["status"]["state"] == "declined"
    assert (signer["state"], signer["method"], signer["signature_file_id"]) == (
        "declined",
        "on_screen",
        None,
    )
    assert signer["comment"] == "The scratch is not mine"


def test_invalid_inspection_keeps_signatures_and_new_one_has_its_own_act(
    owner: Client, s: Scenario
) -> None:
    rental, inspection = _issued(owner, s)
    d = _made(owner, s, inspection)
    _sign(owner, s, d)
    ok(do(owner, s, inspection, "invalidate", reason="Readings of another car"))
    kept = _signing(owner, s, d)
    assert kept["status"]["state"] == "signed"
    assert kept["signers"][0]["signature_file_id"]
    assert code(_act(owner, s, inspection)) == "INSPECTION_ACT_NOT_SAVED"
    version = owner.get(f"/api/c/{s.cmp}/documents/{d['id']}").json()["document"]["version"]
    url = f"/api/c/{s.cmp}/documents/{d['id']}/remake"
    remade = post(owner, url, {"expected_version": version}, key())
    assert code(remade) == "INSPECTION_ACT_NOT_SAVED"

    fresh = created(
        owner,
        s,
        kind="issue",
        application="rental",
        rental_id=rental["id"],
        performed_at=NOW.isoformat(),
        odometer="10001",
        fuel="8",
    )
    fresh = ok(do(owner, s, fresh, "complete", conducted=True))
    second = _made(owner, s, fresh)
    signers = _signing(owner, s, second)["signers"]
    assert [x["state"] for x in signers] == ["pending"]
    assert all(x["signature_file_id"] is None for x in signers)


def test_signed_act_meets_the_requirement_of_its_stage(owner: Client, s: Scenario) -> None:
    kinds = owner.get(f"/api/c/{s.cmp}/document-kinds").json()["kinds"]
    kind = next(k["id"] for k in kinds if k["code"] == "inspection_act")
    body = {"stage": "at_return", "need": "signed", "kind_id": kind}
    assert post(owner, f"/api/c/{s.cmp}/document-requirements", body, key()).status_code == 201
    rental, issued = _issued(owner, s)
    # The act of the issue does not count for the return.
    _sign(owner, s, _made(owner, s, issued))
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
    )
    assert returned.status_code == 200, returned.content
    assert returned.json()["rental"]["formalization"]["missing"] == ["return_documents_missing"]
    found = owner.get(f"/api/c/{s.cmp}/inspections?rental_id={rental['id']}").json()
    back = next(i for i in found["inspections"] if i["kind"] == "return")
    act = _made(owner, s, back)
    pending = owner.get(f"/api/c/{s.cmp}/rentals/{rental['id']}").json()["rental"]
    assert pending["formalization"]["missing"] == ["return_documents_missing"]
    _sign(owner, s, act)
    done = owner.get(f"/api/c/{s.cmp}/rentals/{rental['id']}").json()["rental"]
    assert done["formalization"]["missing"] == []


def test_rights_and_allowed_ways(owner: Client, s: Scenario) -> None:
    _, inspection = _issued(owner, s)
    viewer, _ = hire(
        owner,
        s.cmp,
        "viewer@example.com",
        [
            {"type": "grant", "permission": "documents.view"},
            {"type": "grant", "permission": "vehicle.view"},
        ],
    )
    assert _act(viewer, s, inspection).status_code == 403
    blind, _ = hire(
        owner, s.cmp, "blind@example.com", [{"type": "grant", "permission": "documents.create"}]
    )
    assert _act(blind, s, inspection).status_code == 404
    d = _made(owner, s, inspection)
    file_id = _signature(viewer).json()["file"]["id"]
    denied = _on_screen(viewer, s, d, decision="sign", consent=True, file_id=file_id)
    assert denied.status_code == 403

    # The way is allowed by the kind (§ 18.4.2).
    body = {"kind_id": d["kind_id"], "methods": "both", "on_screen": False}
    changed = post(owner, f"/api/c/{s.cmp}/document-kinds/signing", body, key())
    assert changed.status_code == 200, changed.content
    listed = owner.get(f"/api/c/{s.cmp}/document-kinds/signing").json()["kinds"]
    assert next(k for k in listed if k["kind_id"] == d["kind_id"])["on_screen"] is False
    own = _signature(owner).json()["file"]["id"]
    refused = _on_screen(owner, s, d, decision="sign", consent=True, file_id=own)
    assert code(refused) == "SIGNING_METHOD_NOT_ALLOWED"
    assert _signing(owner, s, d)["on_screen"] is False
