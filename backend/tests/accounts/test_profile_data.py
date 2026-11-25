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
    "documents": [
        {
            "type": "passport",
            "number": "AB 1234567",
            "issued_on": "2020-01-10",
            "expires_on": "2030-01-10",
        }
    ],
}
PASSPORT = {
    "type": "passport",
    "title": "",
    "number": "AB 1234567",
    "issued_on": "2020-01-10",
    "expires_on": "2030-01-10",
    "issued_by": "",
    "categories": [],
}


def documents(*changes: dict[str, Any]) -> list[dict[str, Any]]:
    return [{**PASSPORT, **change} for change in changes]


def test_first_entry_saves_all_personal_data_without_code(client: Client) -> None:
    register(client)

    response = post(client, "/api/me/personal-data", FULL, key())

    assert response.status_code == 200, response.content
    user = response.json()["user"]
    assert user["birth_date"] == "1990-05-17"
    assert user["contact_email"] == "anna.contact@example.com"
    assert user["documents"] == [PASSPORT]
    assert user["email"] == "owner@example.com"  # the login email is not touched


def test_later_change_needs_code_and_journals_only_changed_fields(client: Client) -> None:
    register(client)
    post(client, "/api/me/personal-data", FULL, key())

    changed = {**FULL, "documents": documents({"number": "X1"})}
    refused = post(client, "/api/me/personal-data", changed, key())
    assert refused.status_code == 400  # flow and code are required

    flow = code_for(client, "change_personal_data")
    body = {**changed, "flow": flow, "code": CODE}
    assert post(client, "/api/me/personal-data", body, key()).status_code == 200
    event = AuditEvent.objects.filter(action="account.personal_data_changed").latest("occurred_at")
    assert event.before == {"documents": [PASSPORT]}
    assert event.after == {"documents": documents({"number": "X1"})}


@pytest.mark.parametrize(
    ("changes", "field", "code"),
    [
        ({"birth_date": "2999-01-01"}, "birth_date", "DATE_IN_FUTURE"),
        ({"birth_date": "17.05.1990"}, "birth_date", "DATE_INVALID"),
        ({"contact_email": "not-an-email"}, "email", "EMAIL_INVALID"),
        ({"contact_phone": "call me"}, "contact_phone", "PHONE_INVALID"),
        (
            {"documents": documents({"expires_on": "2019-01-10"})},
            "documents[0].expires_on",
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


def test_the_documents_are_a_list_of_chosen_kinds(client: Client) -> None:
    """Customer's remarks of 25–26.11.2026: the person adds the documents they wish, each of a
    chosen kind — a passport, an ID card, a driving licence with its categories, another one; who
    issued each one; none is required."""
    register(client)
    none = post(client, "/api/me/personal-data", {**FULL, "documents": []}, key())
    assert none.json()["user"]["documents"] == []

    flow = code_for(client, "change_personal_data")
    listed = [
        {"type": "national_id", "number": "ID 1", "issued_by": "ОВД района Хамовники"},
        {
            "type": "driver_license",
            "number": "77 01 123456",
            "issued_on": "2019-03-04",
            "expires_on": "2029-03-04",
            "issued_by": "ГИБДД 7701",
            "categories": ["b", "C1E", "B", " "],
        },
        {"type": "other", "title": "Страховка", "number": "S-9"},
        # Categories belong to a licence only.
        {"type": "passport", "number": "P 2", "categories": ["B"]},
    ]
    body = {**FULL, "documents": listed, "flow": flow, "code": CODE}
    response = post(client, "/api/me/personal-data", body, key())
    assert response.status_code == 200, response.content
    found = response.json()["user"]["documents"]
    assert [d["type"] for d in found] == ["national_id", "driver_license", "other", "passport"]
    assert found[0]["issued_by"] == "ОВД района Хамовники"
    assert found[1]["categories"] == ["B", "C1E"] and found[1]["issued_by"] == "ГИБДД 7701"
    assert found[2]["title"] == "Страховка"
    assert found[3]["categories"] == []

    for wrong, field, code in (
        ({"type": "visa", "number": "1"}, "documents[0].type", "VALUE_UNKNOWN"),
        ({"type": "passport", "number": ""}, "documents[0].number", "FIELD_REQUIRED"),
        ({"type": "other", "number": "1"}, "documents[0].title", "FIELD_REQUIRED"),
        (
            {"type": "driver_license", "number": "1", "categories": "B"},
            "documents[0].categories",
            "VALIDATION_ERROR",
        ),
    ):
        refused = post(client, "/api/me/personal-data", {**FULL, "documents": [wrong]}, key())
        error = refused.json()["error"]
        assert error["field"] == field, error
        if code != "VALIDATION_ERROR":
            assert error["code"] == code, error


def test_the_documents_entered_before_go_into_the_list() -> None:
    """Customer's remark of 26.11.2026: the passport or ID and the licence entered before as
    separate fields become documents of the list."""
    import importlib
    from datetime import date

    migration = importlib.import_module("c4rsys.accounts.migrations.0010_profile_documents_list")
    row = {
        "document_type": "national_id",
        "document_number": "ID 1",
        "document_issued_on": date(2020, 1, 2),
        "document_expires_on": None,
        "document_issued_by": "ОВД",
        "licence_number": "77 01",
        "licence_issued_on": None,
        "licence_expires_on": date(2030, 1, 2),
        "licence_issued_by": "",
        "licence_categories": ["B"],
    }
    assert migration.documents_of(row) == [
        {
            "type": "national_id",
            "title": "",
            "number": "ID 1",
            "issued_on": "2020-01-02",
            "expires_on": None,
            "issued_by": "ОВД",
            "categories": [],
        },
        {
            "type": "driver_license",
            "title": "",
            "number": "77 01",
            "issued_on": None,
            "expires_on": "2030-01-02",
            "issued_by": "",
            "categories": ["B"],
        },
    ]
    assert migration.documents_of({**row, "document_number": "", "licence_number": ""}) == []
