"""Sessions, their account-wide limits and the own profile (ТЗ § 2.1.1, § 2.3.3, § 24.4.2)."""

from datetime import timedelta
from typing import Any

import pytest
from django.test import Client
from django.utils import timezone

from c4rsys.accounts.models import UserAccount, UserSession
from c4rsys.platform.models import AuditEvent
from tests.accounts.helpers import CODE, login, post, register

pytestmark = pytest.mark.django_db

KEY = {"Idempotency-Key": "k-1"}


def _age_session(*, created_minutes: int = 0, idle_minutes: int = 0) -> None:
    now = timezone.now()
    UserSession.objects.update(
        created_at=now - timedelta(minutes=created_minutes),
        last_activity_at=now - timedelta(minutes=idle_minutes),
    )


def test_defaults_are_seven_and_thirty_days(client: Client) -> None:
    register(client)

    body = client.get("/api/me/session-settings").json()

    assert body["idle_minutes"] == 7 * 24 * 60
    assert body["total_minutes"] == 30 * 24 * 60


def test_session_ends_after_idle_limit(client: Client) -> None:
    register(client)
    _age_session(idle_minutes=7 * 24 * 60 + 1)

    response = client.get("/api/me")

    assert response.status_code == 401
    assert UserSession.objects.get().end_reason == "idle_expired"


def test_session_ends_after_total_limit_even_if_active(client: Client) -> None:
    register(client)
    _age_session(created_minutes=30 * 24 * 60 + 1, idle_minutes=0)

    assert client.get("/api/me").status_code == 401
    assert UserSession.objects.get().end_reason == "total_expired"


def test_user_action_extends_activity_background_request_does_not(client: Client) -> None:
    register(client)
    _age_session(idle_minutes=10)
    before = UserSession.objects.get().last_activity_at

    client.get("/api/me", headers={"X-C4R-Background": "1"})
    assert UserSession.objects.get().last_activity_at == before

    client.get("/api/me")
    assert UserSession.objects.get().last_activity_at > before


def test_shorter_limits_apply_to_already_open_sessions(client: Client) -> None:
    # § 24.4.2: settings apply to open sessions; the count is not restarted.
    register(client)
    other = Client()
    login(other)

    response = client.put(
        "/api/me/session-settings",
        data={"idle_minutes": 60, "total_minutes": 120},
        content_type="application/json",
        headers=KEY,
    )
    assert response.status_code == 200

    UserSession.objects.update(created_at=timezone.now() - timedelta(minutes=121))
    assert other.get("/api/me").status_code == 401
    assert client.get("/api/me").status_code == 401


@pytest.mark.parametrize(
    ("idle", "total", "field"),
    [
        (0, 60, "idle_minutes"),
        (7 * 24 * 60 + 1, 60, "idle_minutes"),
        (60, 30 * 24 * 60 + 1, "total_minutes"),
    ],
)
def test_limits_outside_range_are_rejected(
    client: Client, idle: int, total: int, field: str
) -> None:
    register(client)

    response = client.put(
        "/api/me/session-settings",
        data={"idle_minutes": idle, "total_minutes": total},
        content_type="application/json",
        headers=KEY,
    )

    assert response.status_code == 400
    assert response.json()["error"]["field"] == field


def test_reset_restores_defaults_without_resetting_session_times(client: Client) -> None:
    register(client)
    client.put(
        "/api/me/session-settings",
        data={"idle_minutes": 60, "total_minutes": 120},
        content_type="application/json",
        headers=KEY,
    )
    created = UserSession.objects.get().created_at

    response = post(client, "/api/me/session-settings/reset", headers={"Idempotency-Key": "k-2"})

    assert response.json()["idle_minutes"] == 7 * 24 * 60
    assert UserSession.objects.get().created_at == created


def test_command_without_idempotency_key_is_rejected(client: Client) -> None:
    register(client)

    response = client.patch("/api/me", data={"theme": "dark"}, content_type="application/json")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"


def test_language_and_theme_are_saved_in_profile(client: Client) -> None:
    register(client)

    response = client.patch(
        "/api/me",
        data={"language": "th", "theme": "dark"},
        content_type="application/json",
        headers=KEY,
    )

    assert response.status_code == 200
    user = UserAccount.objects.get()
    assert (user.language, user.theme) == ("th", "dark")
    assert AuditEvent.objects.filter(action="account.settings_changed").count() == 1


def _set_name(client: Client, key: str, **extra: str) -> Any:
    data = {"first_name": "Иван", "last_name": "Петров", **extra}
    return post(client, "/api/me/personal-data", data, headers={"Idempotency-Key": key})


def test_first_name_entry_after_registration_needs_no_code(client: Client) -> None:
    # Decision 22.10.2026: the first entry of the name does not require a code.
    register(client)

    response = _set_name(client, "k-1")

    assert response.status_code == 200
    assert response.json()["user"]["has_full_name"] is True


def test_changing_an_existing_name_requires_a_code(client: Client) -> None:
    # § 2.1.1: changing personal data needs a separate one-time code.
    register(client)
    _set_name(client, "k-1")

    without_code = _set_name(client, "k-2", first_name="Пётр")
    assert without_code.status_code == 400
    assert without_code.json()["error"]["field"] == "flow"

    flow = post(client, "/api/me/confirmations", {"action": "change_personal_data"}).json()["flow"]
    wrong = _set_name(client, "k-3", first_name="Пётр", flow=flow, code="000000")
    assert wrong.json()["error"]["code"] == "CODE_INVALID"
    assert UserAccount.objects.get().first_name == "Иван"

    ok = _set_name(client, "k-4", first_name="Пётр", flow=flow, code=CODE)
    assert ok.status_code == 200
    assert UserAccount.objects.get().first_name == "Пётр"


def test_sessions_list_and_end_one(client: Client) -> None:
    register(client)
    other = Client()
    login(other)

    listed = client.get("/api/me/sessions").json()["sessions"]
    assert len(listed) == 2
    foreign = next(item for item in listed if not item["current"])

    response = post(client, f"/api/me/sessions/{foreign['id']}/end", headers=KEY)

    assert response.status_code == 200
    assert other.get("/api/me").status_code == 401
    assert client.get("/api/me").status_code == 200


def test_end_all_sessions_requires_a_code_and_ends_current_too(client: Client) -> None:
    register(client)
    other = Client()
    login(other)
    flow = post(client, "/api/me/confirmations", {"action": "end_all_sessions"}).json()["flow"]

    response = post(client, "/api/me/sessions/end-all", {"flow": flow, "code": CODE}, headers=KEY)

    assert response.json()["count"] == 2
    assert other.get("/api/me").status_code == 401
    assert client.get("/api/me").status_code == 401


def test_logout_ends_the_session(client: Client) -> None:
    register(client)

    post(client, "/api/auth/logout")

    assert client.get("/api/me").status_code == 401
    assert UserSession.objects.get().end_reason == "logout"


def test_anonymous_request_needs_a_session(client: Client) -> None:
    response = client.get("/api/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "SESSION_REQUIRED"
