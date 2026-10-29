"""Documents, part Р3: signing of a version (ТЗ § 18.3, § 18.4.2; customer's decisions Р-4, Р-6
of 29.10.2026)."""

from collections.abc import Iterator
from datetime import timedelta
from typing import Any
from unittest import mock

import pytest
from django.test import Client
from django.utils import timezone

from c4rsys.documents.models import SigningInvitation
from tests.accounts.helpers import CODE, post
from tests.companies.helpers import hire, key
from tests.documents.test_registry import _create, _file, _kind, _ok
from tests.rentals.scenario import Scenario

pytestmark = pytest.mark.django_db

EMAIL = "ivan@example.com"


@pytest.fixture
def links() -> Iterator[list[str]]:
    """Urls of the sent personal links, in the order of sending."""
    sent: list[str] = []
    with mock.patch(
        "c4rsys.accounts.mail.send_signing_link",
        side_effect=lambda email, company, title, url: sent.append(url),
    ):
        yield sent


@pytest.fixture
def boss(owner: Client, s: Scenario) -> tuple[Client, str]:
    rights = [
        {"type": "grant", "permission": "documents.view"},
        {"type": "grant", "permission": "documents.sign"},
    ]
    return hire(owner, s.cmp, "boss@example.com", rights)


@pytest.fixture
def doc(owner: Client, s: Scenario) -> dict[str, Any]:
    owner.patch(
        f"/api/c/{s.cmp}/clients/{s.client}",
        data={
            "contacts": [
                {"kind": "phone", "value": "+66812345678"},
                {"kind": "email", "value": EMAIL},
            ],
            "expected_version": 1,
        },
        content_type="application/json",
        headers=key(),
    )
    link = {"object_type": "client", "object_id": s.client}
    return _ok(_create(owner, s, links=[link]), 201)


def _signing(client: Client, s: Scenario, d: dict[str, Any]) -> dict[str, Any]:
    found: dict[str, Any] = client.get(f"/api/c/{s.cmp}/documents/{d['id']}/signing").json()[
        "signing"
    ]
    return found


def _send(client: Client, s: Scenario, d: dict[str, Any], *people: tuple[str, str]) -> Any:
    version = client.get(f"/api/c/{s.cmp}/documents/{d['id']}").json()["document"]["version"]
    body = {"expected_version": version, "signers": [{"role": r, "id": i} for r, i in people]}
    return post(client, f"/api/c/{s.cmp}/documents/{d['id']}/signing", body, key())


def _signer(client: Client, s: Scenario, d: dict[str, Any], role: str) -> dict[str, Any]:
    found: dict[str, Any] = next(x for x in _signing(client, s, d)["signers"] if x["role"] == role)
    return found


def _act(
    client: Client, s: Scenario, d: dict[str, Any], signer: str, action: str, **body: Any
) -> Any:
    url = f"/api/c/{s.cmp}/documents/{d['id']}/signers/{signer}/{action}"
    return post(client, url, body, key())


def _token(url: str) -> str:
    return url.rsplit("/", 1)[1]


def _invite(owner: Client, s: Scenario, d: dict[str, Any], links: list[str], capture: Any) -> str:
    signer = _signer(owner, s, d, "client")
    with capture(execute=True):
        sent = _act(owner, s, d, signer["id"], "invite", email=EMAIL)
    assert sent.status_code == 200, sent.content
    return _token(links[-1])


def _by_link(token: str, action: str = "", **body: Any) -> Any:
    anyone = Client()
    if not action:
        return anyone.get(f"/api/sign/{token}")
    return post(anyone, f"/api/sign/{token}/{action}", body, key())


def test_company_by_code_and_client_by_link(
    owner: Client,
    s: Scenario,
    doc: dict[str, Any],
    boss: tuple[Client, str],
    links: list[str],
    django_capture_on_commit_callbacks: Any,
) -> None:
    signer_client, employment = boss
    sent = _send(owner, s, doc, ("company", employment), ("client", s.client))
    assert sent.status_code == 200, sent.content
    assert sent.json()["signing"]["status"] == {"state": "pending", "version": 1}

    company = _signer(signer_client, s, doc, "company")
    assert company["is_me"] and company["name"]
    assert _act(signer_client, s, doc, company["id"], "code").status_code == 200
    wrong = _act(signer_client, s, doc, company["id"], "decide", code="000000", decision="sign")
    assert wrong.status_code == 422
    assert wrong.json()["error"]["params"] == {"attempts_left": 4}
    signed = _act(signer_client, s, doc, company["id"], "decide", code=CODE, decision="sign")
    assert signed.status_code == 200, signed.content
    company = _signer(owner, s, doc, "company")
    assert company["state"] == "signed" and company["method"] == "in_app"
    assert company["confirmed_contact"] == "boss@example.com"
    # § 18.3.6: one of two signatures is not «signed».
    assert _signing(owner, s, doc)["status"]["state"] == "pending"

    token = _invite(owner, s, doc, links, django_capture_on_commit_callbacks)
    page = _by_link(token).json()["link"]
    assert page["title"] == "Insurance" and page["has_file"] and page["email"] == "i***@example.com"
    # § 18.3.10: the page opens the version only, nothing of the client or the order.
    assert "client_id" not in page and "links" not in page
    assert Client().get(f"/api/sign/{token}/file").content.startswith(b"%PDF")
    assert _by_link(token, "code").status_code == 200
    decided = _by_link(token, "decide", code=CODE, decision="sign")
    assert decided.status_code == 200, decided.content
    client_signer = _signer(owner, s, doc, "client")
    assert client_signer["method"] == "link" and client_signer["confirmed_contact"] == EMAIL
    status = owner.get(f"/api/c/{s.cmp}/documents/{doc['id']}").json()["document"]["signing"]
    assert status == {"state": "signed", "version": 1}
    # § 18.3.9: after the answer the link is not used again.
    assert _by_link(token).json()["error"]["code"] == "SIGNING_LINK_USED"


def test_new_version_does_not_inherit_and_revokes_links(
    owner: Client,
    s: Scenario,
    doc: dict[str, Any],
    links: list[str],
    django_capture_on_commit_callbacks: Any,
) -> None:
    _send(owner, s, doc, ("client", s.client))
    token = _invite(owner, s, doc, links, django_capture_on_commit_callbacks)
    current = owner.get(f"/api/c/{s.cmp}/documents/{doc['id']}").json()["document"]
    body = {"expected_version": current["version"], "file_id": _file(owner), "note": "fixed"}
    assert (
        post(owner, f"/api/c/{s.cmp}/documents/{doc['id']}/versions", body, key()).status_code
        == 200
    )
    # § 18.3.5: a draft of a new version revokes nothing.
    assert _by_link(token).status_code == 200
    assert _send(owner, s, doc, ("client", s.client)).status_code == 200
    assert _by_link(token).json()["error"]["code"] == "SIGNING_LINK_REVOKED"
    signing = _signing(owner, s, doc)
    assert signing["status"] == {"state": "pending", "version": 2}
    assert [(x["version_number"], x["state"]) for x in signing["signers"]] == [
        (2, "pending"),
        (1, "pending"),
    ]
    again = _send(owner, s, doc, ("client", s.client))
    assert again.json()["error"]["code"] == "DOCUMENT_VERSION_ALREADY_SENT"


def test_decline_paper_and_allowed_methods(
    owner: Client,
    s: Scenario,
    doc: dict[str, Any],
    links: list[str],
    django_capture_on_commit_callbacks: Any,
) -> None:
    _send(owner, s, doc, ("client", s.client))
    token = _invite(owner, s, doc, links, django_capture_on_commit_callbacks)
    _by_link(token, "code")
    declined = _by_link(token, "decide", code=CODE, decision="decline", comment="Wrong price")
    assert declined.json() == {"state": "declined"}
    signer = _signer(owner, s, doc, "client")
    assert signer["state"] == "declined" and signer["comment"] == "Wrong price"
    assert _signing(owner, s, doc)["status"]["state"] == "declined"

    current = owner.get(f"/api/c/{s.cmp}/documents/{doc['id']}").json()["document"]
    body = {"expected_version": current["version"], "file_id": _file(owner)}
    post(owner, f"/api/c/{s.cmp}/documents/{doc['id']}/versions", body, key())
    _send(owner, s, doc, ("client", s.client))
    signer = _signer(owner, s, doc, "client")
    kind = {"kind_id": _kind(owner, s), "methods": "in_app"}
    assert post(owner, f"/api/c/{s.cmp}/document-kinds/signing", kind, key()).status_code == 200
    today = timezone.localdate().isoformat()
    paper = {"file_id": _file(owner), "signed_on": today}
    refused = _act(owner, s, doc, signer["id"], "paper", **paper)
    assert refused.json()["error"]["code"] == "SIGNING_METHOD_NOT_ALLOWED"
    kind["methods"] = "paper"
    post(owner, f"/api/c/{s.cmp}/document-kinds/signing", kind, key())
    invite = _act(owner, s, doc, signer["id"], "invite", email=EMAIL)
    assert invite.json()["error"]["code"] == "SIGNING_METHOD_NOT_ALLOWED"
    entered = _act(owner, s, doc, signer["id"], "paper", **{**paper, "file_id": _file(owner)})
    assert entered.status_code == 200, entered.content
    signer = _signer(owner, s, doc, "client")
    assert signer["method"] == "paper" and signer["paper_signed_on"] == today
    assert signer["entered_at"] is not None
    assert _signing(owner, s, doc)["status"] == {"state": "signed", "version": 2}
    # The scan is read by whoever may view the document.
    assert owner.get(f"/api/files/{signer['paper_file_id']}").status_code == 200


def test_links_term_revoke_and_card_email(
    owner: Client,
    s: Scenario,
    doc: dict[str, Any],
    links: list[str],
    django_capture_on_commit_callbacks: Any,
) -> None:
    _send(owner, s, doc, ("client", s.client))
    signer = _signer(owner, s, doc, "client")
    assert signer["emails"] == [EMAIL]
    other = _act(owner, s, doc, signer["id"], "invite", email="other@example.com")
    assert other.json()["error"]["code"] == "SIGNER_EMAIL_NOT_IN_CARD"
    token = _invite(owner, s, doc, links, django_capture_on_commit_callbacks)
    assert _signer(owner, s, doc, "client")["link"]["email"] == EMAIL
    assert _act(owner, s, doc, signer["id"], "revoke").status_code == 200
    assert _by_link(token).json()["error"]["code"] == "SIGNING_LINK_REVOKED"
    # § 18.3.8: after the revocation a new link to the same signer and version.
    token = _invite(owner, s, doc, links, django_capture_on_commit_callbacks)
    SigningInvitation.objects.filter(state="active").update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )
    assert _by_link(token).json()["error"]["code"] == "SIGNING_LINK_EXPIRED"
    assert _by_link("unknown-token").status_code == 404


def test_roles_of_template_and_rights(
    owner: Client, s: Scenario, doc: dict[str, Any], boss: tuple[Client, str]
) -> None:
    signer_client, employment = boss
    viewer, other = hire(
        owner, s.cmp, "viewer@example.com", [{"type": "grant", "permission": "documents.view"}]
    )
    assert _send(viewer, s, doc, ("client", s.client)).status_code == 403
    _send(owner, s, doc, ("company", other), ("client", s.client))
    company = _signer(owner, s, doc, "company")
    # The right to sign differs from preparing (§ 18.3.3); the assigned employee signs.
    assert _act(viewer, s, doc, company["id"], "code").status_code == 403
    not_me = _act(signer_client, s, doc, company["id"], "code")
    assert not_me.json()["error"]["code"] == "SIGNER_NOT_YOU"

    kind = _kind(owner, s)
    own = post(
        owner,
        f"/api/c/{s.cmp}/document-templates",
        {
            "title": "Consent",
            "kind_id": kind,
            "source": "client",
            "content": "{{client.full_name}}",
            "signers": {"company": True, "client": True},
        },
        key(),
    ).json()["template"]
    made = post(
        owner,
        f"/api/c/{s.cmp}/documents/from-template",
        {"template_id": own["id"], "source_id": s.client},
        key(),
    ).json()["document"]
    missing = _send(owner, s, made, ("client", s.client))
    assert missing.json()["error"]["code"] == "SIGNER_ROLE_MISSING"
    assert missing.json()["error"]["params"] == {"roles": ["company"]}
    assert _signing(owner, s, made)["template_signers"] == ["client", "company"]
