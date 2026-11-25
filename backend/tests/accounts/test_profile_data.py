"""Profile data, username, cloud password and login email (ТЗ § 2.1.5–2.1.6, § 2.2, § 2.3.2;
customer's decisions of 24.10.2026)."""

from datetime import timedelta
from typing import Any

import pytest
from django.contrib.auth.hashers import check_password
from django.test import Client
from django.utils import timezone

from c4rsys.accounts.models import LoginContact, UserAccount, UserSession
from c4rsys.platform.models import AuditEvent
from tests.accounts.helpers import CODE, PASSWORD, login, post, register
from tests.files.helpers import upload

pytestmark = pytest.mark.django_db

_keys = iter(range(10**6))
NEW_PASSWORD = "Copper-Lantern-2027"


def key() -> dict[str, str]:
    return {"Idempotency-Key": f"pd-{next(_keys)}"}


def code_for(client: Client, action: str) -> str:
    response = post(client, "/api/me/confirmations", {"action": action})
    assert response.status_code == 200, response.content
    flow: str = response.json()["flow"]
    return flow


FULL = {
    "last_name": "Petrova",
    "first_name": "Anna",
    "middle_name": "Sergeevna",
    "birth_date": "1990-05-17",
    "contact_email": "Anna.Contact@Example.com",
    "contact_phone": "+66 81 234 5678",
    "document_type": "passport",
    "document_number": "AB 1234567",
    "document_issued_on": "2020-01-10",
    "document_expires_on": "2030-01-10",
}


def test_first_entry_saves_all_personal_data_without_code(client: Client) -> None:
    register(client)

    response = post(client, "/api/me/personal-data", FULL, key())

    assert response.status_code == 200, response.content
    user = response.json()["user"]
    assert user["birth_date"] == "1990-05-17"
    assert user["contact_email"] == "anna.contact@example.com"
    assert user["document_expires_on"] == "2030-01-10"
    assert user["email"] == "owner@example.com"  # the login email is not touched


def test_later_change_needs_code_and_journals_only_changed_fields(client: Client) -> None:
    register(client)
    post(client, "/api/me/personal-data", FULL, key())

    refused = post(client, "/api/me/personal-data", {**FULL, "document_number": "X1"}, key())
    assert refused.status_code == 400  # flow and code are required

    flow = code_for(client, "change_personal_data")
    body = {**FULL, "document_number": "X1", "flow": flow, "code": CODE}
    assert post(client, "/api/me/personal-data", body, key()).status_code == 200
    event = AuditEvent.objects.filter(action="account.personal_data_changed").latest("occurred_at")
    assert event.before == {"document_number": "AB 1234567"}
    assert event.after == {"document_number": "X1"}


@pytest.mark.parametrize(
    ("changes", "field", "code"),
    [
        ({"birth_date": "2999-01-01"}, "birth_date", "DATE_IN_FUTURE"),
        ({"birth_date": "17.05.1990"}, "birth_date", "DATE_INVALID"),
        ({"contact_email": "not-an-email"}, "email", "EMAIL_INVALID"),
        ({"contact_phone": "call me"}, "contact_phone", "PHONE_INVALID"),
        (
            {"document_issued_on": "2020-01-10", "document_expires_on": "2019-01-10"},
            "document_expires_on",
            "DATE_BEFORE_ISSUE",
        ),
    ],
)
def test_invalid_personal_data_is_refused(
    client: Client, changes: dict[str, str], field: str, code: str
) -> None:
    register(client)

    response = post(client, "/api/me/personal-data", {**FULL, **changes}, key())

    assert response.status_code == 400
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["field"] == field


def test_avatar_is_set_from_own_upload(client: Client) -> None:
    register(client)
    avatar = upload(client, "avatar").json()["file"]["id"]

    response = post(client, "/api/me/personal-data", {**FULL, "avatar": avatar}, key())

    assert response.json()["user"]["avatar"] == avatar


def test_timezone_is_a_personal_setting(client: Client) -> None:
    register(client)

    def patch(zone: str) -> Any:
        return client.patch(
            "/api/me", data={"timezone": zone}, content_type="application/json", headers=key()
        )

    assert patch("Asia/Yekaterinburg").json()["user"]["timezone"] == "Asia/Yekaterinburg"
    assert patch("Mars/Olympus").json()["error"]["code"] == "TIMEZONE_INVALID"


# --- Username (§ 2.2.1–2.2.2) ---------------------------------------------------------------


def put_username(client: Client, value: str) -> Any:
    return client.put(
        "/api/me/username",
        data={"username": value},
        content_type="application/json",
        headers=key(),
    )


def test_username_is_unique_without_case(client: Client) -> None:
    register(client)
    assert put_username(client, "Artem").json()["user"]["username"] == "Artem"
    other = Client()
    register(other, "other@example.com")

    response = put_username(other, "artem")

    assert response.json()["error"]["code"] == "USERNAME_TAKEN"


@pytest.mark.parametrize("value", ["ab", "a" * 31, "with space", "кириллица"])
def test_username_format(client: Client, value: str) -> None:
    register(client)

    assert put_username(client, value).json()["error"]["code"] in {
        "USERNAME_INVALID",
        "FIELD_TOO_LONG",
    }


def test_username_changes_once_in_30_days_and_old_name_stays_reserved(client: Client) -> None:
    register(client)
    put_username(client, "first.name")
    put_username(client, "second")  # the first name was set, not changed
    assert put_username(client, "third").json()["error"]["code"] == "USERNAME_CHANGE_TOO_SOON"

    other = Client()
    register(other, "other@example.com")
    assert put_username(other, "first.name").json()["error"]["code"] == "USERNAME_TAKEN"

    UserAccount.objects.filter(username="second").update(
        username_changed_at=timezone.now() - timedelta(days=31)
    )
    assert put_username(client, "third").status_code == 200


# --- Cloud password (decision of 24.10.2026) ------------------------------------------------


def change_password(client: Client, current: str, flow: str) -> Any:
    body = {"current_password": current, "new_password": NEW_PASSWORD, "flow": flow, "code": CODE}
    return post(client, "/api/me/password", body, key())


def test_password_change_needs_current_password_and_code(client: Client) -> None:
    register(client)

    without_code = post(
        client,
        "/api/me/password",
        {"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        key(),
    )
    assert without_code.status_code == 400

    wrong = change_password(client, "Wrong-Password-1", code_for(client, "change_password"))
    assert wrong.json()["error"]["code"] == "CURRENT_PASSWORD_WRONG"
    assert login(Client()).status_code == 200  # the old password still works


def test_password_change_ends_other_sessions_and_keeps_current(client: Client) -> None:
    register(client)
    other = Client()
    assert login(other).status_code == 200

    response = change_password(client, PASSWORD, code_for(client, "change_password"))

    assert response.status_code == 200, response.content
    assert response.json()["ended_sessions"] == 1
    assert client.get("/api/me").status_code == 200
    assert other.get("/api/me").status_code == 401
    assert UserSession.objects.filter(end_reason="password_changed").count() == 1
    assert check_password(NEW_PASSWORD, UserAccount.objects.get().password_hash)


# --- Login email (§ 2.3.2, § 2.3.3) ---------------------------------------------------------


def test_email_change_confirms_both_addresses_and_ends_all_sessions(client: Client) -> None:
    register(client)
    started = post(
        client,
        "/api/me/email/start",
        {"flow": code_for(client, "change_email"), "code": CODE, "email": "new@example.com"},
    )
    assert started.status_code == 200, started.content

    done = post(
        client, "/api/me/email/complete", {"flow": started.json()["flow"], "code": CODE}, key()
    )

    assert done.status_code == 200, done.content
    assert LoginContact.objects.get().value == "new@example.com"
    assert client.get("/api/me").status_code == 401
    assert login(Client(), email="new@example.com").status_code == 200


def test_taken_email_is_told_only_after_its_code(client: Client) -> None:
    register(client)
    register(Client(), "taken@example.com")
    started = post(
        client,
        "/api/me/email/start",
        {"flow": code_for(client, "change_email"), "code": CODE, "email": "taken@example.com"},
    )
    assert started.status_code == 200

    done = post(
        client, "/api/me/email/complete", {"flow": started.json()["flow"], "code": CODE}, key()
    )

    assert done.json()["error"]["code"] == "EMAIL_ALREADY_REGISTERED"
    assert LoginContact.objects.filter(value="owner@example.com").exists()


def test_the_passport_and_the_driving_licence_are_kept_apart(client: Client) -> None:
    """Customer's decisions of 25.11.2026: the profile has a passport or an ID card and, apart from
    it, a driving licence with its categories."""
    register(client)
    both = {
        **FULL,
        "licence_number": "77 01 123456",
        "licence_issued_on": "2019-03-04",
        "licence_expires_on": "2029-03-04",
        "licence_categories": ["b", "C1E", "B", " "],
    }
    response = post(client, "/api/me/personal-data", both, key())
    assert response.status_code == 200, response.content
    user = response.json()["user"]
    assert user["document_type"] == "passport" and user["document_number"] == "AB 1234567"
    assert user["licence_number"] == "77 01 123456"
    assert user["licence_categories"] == ["B", "C1E"]
    for wrong, field in (
        ({"licence_categories": "B"}, "licence_categories"),
        ({"document_type": "Водительское удостоверение"}, "document_type"),
        ({"licence_expires_on": "2018-01-01"}, "licence_expires_on"),
    ):
        refused = post(client, "/api/me/personal-data", {**both, **wrong}, key())
        assert refused.json()["error"]["field"] == field


def test_the_document_entered_before_goes_to_its_own_place(client: Client) -> None:
    """Customer's decision of 25.11.2026: the one document of the profile entered before is put
    by its type — a licence to the licence, a passport or an ID card stays as such."""
    import importlib

    from django.apps import apps

    from c4rsys.accounts.models import UserAccount

    migration = importlib.import_module("c4rsys.accounts.migrations.0008_profile_driving_licence")
    register(client)
    user = UserAccount.objects.get()
    for text, kind in (("Паспорт", "passport"), ("ID-карта", "national_id"), ("СНИЛС", "")):
        UserAccount.objects.filter(pk=user.pk).update(document_type=text, document_number="N1")
        migration.split_documents(apps, None)
        found = UserAccount.objects.get(pk=user.pk)
        assert (found.document_type, found.document_number, found.licence_number) == (
            kind,
            "N1",
            "",
        )
    UserAccount.objects.filter(pk=user.pk).update(
        document_type="Водительское удостоверение",
        document_number="77 01",
        document_issued_on="2020-01-02",
    )
    migration.split_documents(apps, None)
    found = UserAccount.objects.get(pk=user.pk)
    assert (found.document_type, found.document_number, found.document_issued_on) == ("", "", None)
    assert found.licence_number == "77 01" and str(found.licence_issued_on) == "2020-01-02"
