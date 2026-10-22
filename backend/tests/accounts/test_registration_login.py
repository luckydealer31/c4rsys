"""Registration and two-step login (ТЗ § 2.1–2.4, § 24.4.2)."""

from datetime import timedelta
from typing import Any
from unittest import mock

import pytest
from django.test import Client
from django.utils import timezone

from c4rsys.accounts.models import AuthFlow, LoginContact, OtpCode, OtpSendLog, UserAccount
from c4rsys.platform.ids import normalize_public_id
from c4rsys.platform.models import AuditEvent
from tests.accounts.helpers import CODE, PASSWORD, login, post, register

pytestmark = pytest.mark.django_db


def test_registration_creates_account_and_signs_in(client: Client) -> None:
    body = register(client, "Owner@Example.com ")

    user = UserAccount.objects.get()
    assert normalize_public_id(user.public_id) == user.public_id
    assert user.public_id.startswith("USR-")
    assert LoginContact.objects.get(user=user).value == "owner@example.com"
    assert user.password_hash.startswith("argon2")
    assert PASSWORD not in user.password_hash
    # Backup code is shown once and stored only as a hash (§ 2.1.3, § 24.4.3).
    assert len(body["backup_code"]) == 19
    assert body["backup_code"] not in user.backup_code_hash
    assert body["user"]["has_full_name"] is False
    assert client.get("/api/me").json()["user"]["public_id"] == user.public_id
    assert AuditEvent.objects.filter(action="account.registered").count() == 1


def test_registering_a_taken_email_is_revealed_only_after_the_code(client: Client) -> None:
    register(client)
    other = Client()
    # The 60-second resend interval applies to the same contact and action (§ 2.4.1).
    OtpSendLog.objects.update(sent_at=timezone.now() - timedelta(seconds=61))

    start = post(other, "/api/auth/register/start", {"email": "owner@example.com"})
    assert start.status_code == 200  # nothing revealed before the code (§ 24.4.2)

    verify = post(other, "/api/auth/register/verify", {"flow": start.json()["flow"], "code": CODE})
    assert verify.status_code == 422
    assert verify.json()["error"]["code"] == "EMAIL_ALREADY_REGISTERED"


def test_invalid_email_is_rejected(client: Client) -> None:
    response = post(client, "/api/auth/register/start", {"email": "not-an-email"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "EMAIL_INVALID"


def test_weak_password_is_rejected(client: Client) -> None:
    flow = post(client, "/api/auth/register/start", {"email": "a@example.com"}).json()["flow"]
    post(client, "/api/auth/register/verify", {"flow": flow, "code": CODE})

    response = post(client, "/api/auth/register/complete", {"flow": flow, "password": "12345"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "PASSWORD_TOO_WEAK"
    assert not UserAccount.objects.exists()


def test_password_cannot_be_set_before_the_code(client: Client) -> None:
    flow = post(client, "/api/auth/register/start", {"email": "a@example.com"}).json()["flow"]

    response = post(client, "/api/auth/register/complete", {"flow": flow, "password": PASSWORD})

    assert response.json()["error"]["code"] == "CODE_NOT_CONFIRMED"


def test_wrong_code_counts_attempts_and_five_exhaust_the_code(client: Client) -> None:
    # § 2.4.1: five attempts.
    flow = post(client, "/api/auth/register/start", {"email": "a@example.com"}).json()["flow"]

    for left in (4, 3, 2, 1):
        error = post(client, "/api/auth/register/verify", {"flow": flow, "code": "000000"})
        assert error.json()["error"]["code"] == "CODE_INVALID"
        assert error.json()["error"]["params"] == {"attempts_left": left}
    last = post(client, "/api/auth/register/verify", {"flow": flow, "code": "000000"})
    assert last.json()["error"]["code"] == "CODE_ATTEMPTS_EXHAUSTED"

    correct_now = post(client, "/api/auth/register/verify", {"flow": flow, "code": CODE})
    assert correct_now.json()["error"]["code"] == "CODE_EXPIRED"


def test_code_expires_after_three_minutes(client: Client) -> None:
    flow = post(client, "/api/auth/register/start", {"email": "a@example.com"}).json()["flow"]
    OtpCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

    response = post(client, "/api/auth/register/verify", {"flow": flow, "code": CODE})

    assert response.json()["error"]["code"] == "CODE_EXPIRED"


def test_code_ttl_and_resend_interval_are_reported(client: Client) -> None:
    body = post(client, "/api/auth/register/start", {"email": "a@example.com"}).json()

    assert body["code_ttl_seconds"] == 180
    assert body["resend_after_seconds"] == 60


def test_resend_is_allowed_after_sixty_seconds_and_annuls_the_old_code(client: Client) -> None:
    flow = post(client, "/api/auth/register/start", {"email": "a@example.com"}).json()["flow"]

    too_early = post(client, "/api/auth/code/resend", {"flow": flow, "purpose": "register"})
    assert too_early.status_code == 429

    OtpSendLog.objects.update(sent_at=timezone.now() - timedelta(seconds=61))
    resent = post(client, "/api/auth/code/resend", {"flow": flow, "purpose": "register"})
    assert resent.status_code == 200
    assert OtpCode.objects.filter(annulled_at__isnull=False).count() == 1
    assert OtpCode.objects.filter(annulled_at__isnull=True).count() == 1


def test_hourly_send_limit_per_contact(client: Client) -> None:
    # § 2.4.4: no more than five sends per hour.
    now = timezone.now()
    for minutes in (50, 40, 30, 20, 10):
        OtpSendLog.objects.create(
            contact_value="a@example.com", purpose="login", sent_at=now - timedelta(minutes=minutes)
        )

    response = post(client, "/api/auth/register/start", {"email": "a@example.com"})

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "OTP_RATE_LIMITED"
    assert not AuthFlow.objects.exists()


def test_daily_send_limit_per_contact(client: Client) -> None:
    # § 2.4.4: no more than ten sends per 24 hours.
    now = timezone.now()
    for hours in range(2, 12):
        OtpSendLog.objects.create(
            contact_value="a@example.com", purpose="login", sent_at=now - timedelta(hours=hours)
        )

    response = post(client, "/api/auth/register/start", {"email": "a@example.com"})

    assert response.status_code == 429


def test_login_with_code_and_password(client: Client) -> None:
    register(client)
    other = Client()

    response = login(other)

    assert response.status_code == 200
    assert other.get("/api/me").status_code == 200
    assert AuditEvent.objects.filter(action="auth.login", result="success").count() == 1


def test_login_for_unknown_email_looks_the_same_and_sends_nothing(
    client: Client, django_capture_on_commit_callbacks: Any
) -> None:
    with mock.patch("c4rsys.accounts.mail.send_code") as send:
        with django_capture_on_commit_callbacks(execute=True):
            response = post(client, "/api/auth/login/start", {"email": "nobody@example.com"})

    assert response.status_code == 200
    assert set(response.json()) == {"flow", "resend_after_seconds", "code_ttl_seconds"}
    send.assert_not_called()
    # A guessed code behaves exactly like a wrong code for an existing account.
    flow = response.json()["flow"]
    verify = post(client, "/api/auth/login/verify", {"flow": flow, "code": "654321"})
    assert verify.json()["error"]["code"] == "CODE_INVALID"


def test_login_code_is_sent_to_existing_account(
    client: Client, django_capture_on_commit_callbacks: Any
) -> None:
    register(client)

    with mock.patch("c4rsys.accounts.mail.send_code") as send:
        with django_capture_on_commit_callbacks(execute=True):
            post(Client(), "/api/auth/login/start", {"email": "owner@example.com"})

    send.assert_called_once_with("owner@example.com", CODE, "login")


def test_wrong_password_is_counted_and_closes_the_flow(client: Client) -> None:
    register(client)
    other = Client()
    flow = post(other, "/api/auth/login/start", {"email": "owner@example.com"}).json()["flow"]
    post(other, "/api/auth/login/verify", {"flow": flow, "code": CODE})

    for left in (4, 3, 2, 1, 0):
        response = post(other, "/api/auth/login/complete", {"flow": flow, "password": "wrong-pass"})
        assert response.json()["error"]["code"] == "LOGIN_FAILED"
        assert response.json()["error"]["params"]["attempts_left"] == left

    final = post(other, "/api/auth/login/complete", {"flow": flow, "password": PASSWORD})
    assert final.json()["error"]["code"] == "FLOW_EXPIRED"
    assert other.get("/api/me").status_code == 401


def test_password_step_requires_the_code_step(client: Client) -> None:
    register(client)
    other = Client()
    flow = post(other, "/api/auth/login/start", {"email": "owner@example.com"}).json()["flow"]

    response = post(other, "/api/auth/login/complete", {"flow": flow, "password": PASSWORD})

    assert response.json()["error"]["code"] == "CODE_NOT_CONFIRMED"


def test_new_login_keeps_other_sessions(client: Client) -> None:
    # § 2.3.3: a normal new login does not end previously opened sessions.
    register(client)
    login(Client())

    assert client.get("/api/me").status_code == 200


@pytest.mark.parametrize(("password", "accepted"), [("Kx7#pQ2", False), ("Kx7#pQ2!", True)])
def test_password_minimum_length_is_eight(client: Client, password: str, accepted: bool) -> None:
    # Technical password policy accepted 22.10.2026: at least 8 characters.
    flow = post(client, "/api/auth/register/start", {"email": "a@example.com"}).json()["flow"]
    post(client, "/api/auth/register/verify", {"flow": flow, "code": CODE})

    response = post(client, "/api/auth/register/complete", {"flow": flow, "password": password})

    assert (response.status_code == 201) is accepted
