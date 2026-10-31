"""Recovery of access through the support (ТЗ § 2.3.1, § 2.3.3; decision В-11.2)."""

from datetime import timedelta
from typing import Any

import pytest
from django.core import mail
from django.test import Client
from django.utils import timezone

from c4rsys.accounts.models import AuthFlow, LoginContact, UserAccount, UserSession
from c4rsys.backoffice import recovery
from c4rsys.backoffice.models import RecoveryCase, SystemJournalEntry
from tests.accounts.helpers import CODE, PASSWORD, login, post
from tests.backoffice.helpers import Member, err, member, sp
from tests.companies.test_ownership import company, transfer, user

pytestmark = pytest.mark.django_db

NEW_PASSWORD = "Copper-Lantern-2027"


@pytest.fixture
def on(settings: Any) -> None:
    settings.C4R_SUPPORT_RECOVERY_ENABLED = True
    settings.C4R_MAIL_MODE = "email"
    settings.C4R_SUPPORT_CONTACT = "support@c4rsys.test"


@pytest.fixture
def verifiers() -> tuple[Member, Member]:
    return (
        member("v1@c4rsys.test", ["verification"], unit="Verification"),
        member("v2@c4rsys.test", ["verification"], unit="Verification"),
    )


def _usr(client: Client) -> str:
    public_id: str = client.get("/api/me").json()["user"]["public_id"]
    return public_id


def _request(identifier: str, known: str, new_email: str = "") -> Any:
    applicant = Client()
    body: dict[str, Any] = {"identifier": identifier, "known_contact": known}
    if new_email:
        flow = post(applicant, "/api/recovery/email/start", {"email": new_email}).json()["flow"]
        verified = post(applicant, "/api/recovery/email/verify", {"flow": flow, "code": CODE})
        assert verified.status_code == 200, verified.content
        body["flow"] = flow
    return post(applicant, "/api/recovery/requests", body)


def _case(number: int) -> RecoveryCase:
    return RecoveryCase.objects.get(number=number)


def test_switched_off_by_the_platform_setting(owner: Client, support: Member) -> None:
    status = Client().get("/api/recovery").json()
    assert status["enabled"] is False
    response = _request(_usr(owner), "owner@example.com")
    assert response.status_code == 403
    assert err(response) == "RECOVERY_DISABLED"
    assert err(support.client.get("/api/staff/recovery")) == "RECOVERY_DISABLED"
    assert not RecoveryCase.objects.exists()


def test_the_request_tells_nothing_about_the_account(on: None, owner: Client) -> None:
    known = _request(_usr(owner), "owner@example.com")
    unknown = _request("USR-00000000", "nobody@example.com")
    assert known.status_code == unknown.status_code == 201
    assert set(known.json()) == set(unknown.json()) == {"number", "support"}
    assert _case(known.json()["number"]).user is not None
    assert _case(unknown.json()["number"]).user is None


def test_two_confirmations_without_the_initiator_then_24_hours(
    on: None,
    owner: Client,
    support: Member,
    verifiers: tuple[Member, Member],
    django_capture_on_commit_callbacks: Any,
) -> None:
    v1, v2 = verifiers
    usr = _usr(owner)
    with django_capture_on_commit_callbacks(execute=True):
        number = _request(usr, "owner@example.com", new_email="fresh@example.com").json()["number"]
    case = _case(number)
    assert case.new_email == "fresh@example.com"
    # The new address is not attached before the check.
    assert not LoginContact.objects.filter(value="fresh@example.com").exists()

    detail = support.client.get(f"/api/staff/recovery/{case.id}").json()["case"]
    assert detail["known_contact_matches"] is True
    old = next(c for c in detail["account_contacts"] if c["kind"] == "email")
    proposed = sp(
        support,
        f"recovery/{case.id}/propose",
        {"assessment": "Profile and history match", "unavailable_contacts": [old["id"]]},
    )
    assert proposed.status_code == 200, proposed.content
    # Support proposes; it may not confirm (no permission) — the verification confirms.
    assert sp(support, f"recovery/{case.id}/approve").status_code == 403
    # The initiator never confirms his own decision.
    proposer = member("p@c4rsys.test", ["verification"])
    again = sp(proposer, f"recovery/{case.id}/propose", {"assessment": "Re-checked"})
    assert again.status_code == 200
    assert err(sp(proposer, f"recovery/{case.id}/approve")) == "RECOVERY_INITIATOR_CONFIRMS"
    sp(
        proposer,
        f"recovery/{case.id}/propose",
        {"assessment": "Profile and history match", "unavailable_contacts": [old["id"]]},
    )
    assert sp(v1, f"recovery/{case.id}/approve").json()["case"]["state"] == "proposed"
    assert err(sp(v1, f"recovery/{case.id}/approve")) == "RECOVERY_ALREADY_CONFIRMED"
    with django_capture_on_commit_callbacks(execute=True):
        approved = sp(v2, f"recovery/{case.id}/approve").json()["case"]
    assert approved["state"] == "approved"
    # The previous contact is told while there is time to object.
    assert any("owner@example.com" in m.to for m in mail.outbox)
    # Nothing changes for 24 hours.
    assert recovery.run(timezone.now()) == 0
    assert UserSession.objects.filter(user__public_id=usr, ended_at__isnull=True).exists()

    mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        assert recovery.run(timezone.now() + timedelta(hours=24, minutes=1)) == 1
    account = UserAccount.objects.get(public_id=usr)
    assert account.recovered_at is not None
    assert LoginContact.objects.filter(user=account, value="fresh@example.com").exists()
    assert LoginContact.objects.get(value="owner@example.com").unavailable_at is not None
    # § 2.3.3: all sessions end.
    assert not UserSession.objects.filter(user=account, ended_at__isnull=True).exists()
    assert owner.get("/api/me").status_code == 401
    # The unavailable contact no longer signs in.
    assert login(Client(), "owner@example.com").status_code != 200
    # The link to set a new cloud password goes to the new address.
    link = next(m for m in mail.outbox if "fresh@example.com" in m.to)
    token = link.body.split("/recover/")[1].split()[0]
    assert Client().get(f"/api/recovery/password/{token}").status_code == 200
    done = post(Client(), "/api/recovery/password", {"token": token, "password": NEW_PASSWORD})
    assert done.status_code == 200, done.content
    assert done.json()["backup_code"]
    # The link works once.
    assert Client().get(f"/api/recovery/password/{token}").status_code == 422
    fresh = Client()
    assert login(fresh, "fresh@example.com", NEW_PASSWORD).status_code == 200
    assert _usr(fresh) == usr
    assert SystemJournalEntry.objects.filter(
        action="recovery.completed", system_process="support_recovery"
    ).exists()


def test_urgent_recovery_has_its_own_basis_and_needs_no_wait(
    on: None, owner: Client, support: Member, verifiers: tuple[Member, Member]
) -> None:
    v1, v2 = verifiers
    number = _request(_usr(owner), "owner@example.com").json()["number"]
    case = _case(number)
    missing = sp(support, f"recovery/{case.id}/propose", {"assessment": "Matches", "urgent": True})
    assert missing.status_code == 400
    sp(
        support,
        f"recovery/{case.id}/propose",
        {"assessment": "Matches", "urgent": True, "urgent_basis": "Account used for fraud now"},
    )
    sp(v1, f"recovery/{case.id}/approve")
    final = sp(v2, f"recovery/{case.id}/approve").json()["case"]
    assert final["state"] == "completed"
    assert UserAccount.objects.get(public_id=_usr_of(case)).recovered_at is not None
    assert AuthFlow.objects.filter(
        purpose=AuthFlow.Purpose.RECOVERY_PASSWORD, completed_at__isnull=True
    ).exists()


def _usr_of(case: RecoveryCase) -> str:
    case.refresh_from_db()
    assert case.user is not None
    return case.user.public_id


def test_refusal_keeps_the_account_closed_to_the_applicant(
    on: None, owner: Client, support: Member, django_capture_on_commit_callbacks: Any
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        number = _request(_usr(owner), "x@example.com", new_email="claim@example.com").json()[
            "number"
        ]
    case = _case(number)
    mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        refused = sp(support, f"recovery/{case.id}/refuse", {"reason": "Not enough information"})
    assert refused.status_code == 200
    assert refused.json()["case"]["state"] == "refused"
    message = next(m for m in mail.outbox if "claim@example.com" in m.to)
    assert "Not enough information" in message.body
    assert "owner@example.com" not in message.body
    assert SystemJournalEntry.objects.filter(action="recovery.refused").exists()
    assert err(sp(support, f"recovery/{case.id}/propose", {"assessment": "x"})) == "RECOVERY_CLOSED"


def test_72_hours_restrict_share_transfers_and_highest_rights(
    on: None, owner: Client, support: Member, verifiers: tuple[Member, Member]
) -> None:
    cmp = company(owner)
    _other, other_id = user("b@example.com")
    v1, v2 = verifiers
    number = _request(_usr(owner), "owner@example.com").json()["number"]
    case = _case(number)
    sp(
        support,
        f"recovery/{case.id}/propose",
        {"assessment": "Matches", "urgent": True, "urgent_basis": "Fraud in progress"},
    )
    sp(v1, f"recovery/{case.id}/approve")
    sp(v2, f"recovery/{case.id}/approve")
    # The sessions ended; the owner signs in again (his email stayed available).
    account = UserAccount.objects.get(public_id=_usr_of(case))
    fresh = Client()
    assert login(fresh, "owner@example.com", PASSWORD).status_code == 200
    response = transfer(fresh, cmp, other_id, "10")
    assert response.status_code == 422
    assert err(response) == "RECENTLY_RECOVERED"
    # After the 72 hours the restriction ends.
    UserAccount.objects.filter(pk=account.pk).update(
        recovered_at=timezone.now() - timedelta(hours=73)
    )
    assert transfer(fresh, cmp, other_id, "10").status_code == 201


def test_the_new_email_waits_for_its_code(on: None) -> None:
    applicant = Client()
    flow = post(applicant, "/api/recovery/email/start", {"email": "n@example.com"}).json()["flow"]
    response = post(
        applicant,
        "/api/recovery/requests",
        {"identifier": "USR-00000000", "known_contact": "x@example.com", "flow": flow},
    )
    assert response.status_code == 422
    assert err(response) == "CODE_NOT_CONFIRMED"
    assert not RecoveryCase.objects.exists()
