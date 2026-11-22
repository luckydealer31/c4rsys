"""The Telegram bot (customer's decisions of 22.11.2026): binding by the link of the profile and
the shared number, the choice of the company, a vehicle by a photo of its paper. Telegram and the
service that reads the papers are replaced by functions of the tests."""

import json
from typing import Any

import pytest
from django.test import Client

from c4rsys.fleet import recognition
from c4rsys.platform.models import AuditEvent
from c4rsys.telegram import api
from c4rsys.telegram.models import TelegramLink
from tests.accounts.helpers import post
from tests.companies.helpers import create_company, key, named_user
from tests.files.helpers import PNG

pytestmark = pytest.mark.django_db

TG = 777001
PAPER: dict[str, Any] = {
    "vehicle_type": "passenger_car",
    "make": "Zzmake",
    "model": "Zzmodel",
    "year": 2022,
    "plate": "AB 1234",
    "plate_country": "TH",
    "vin": "MR2K29F30N1234567",
    "color": "White",
    "fuel_type": "petrol",
    "seats": 5,
    "owner_name": "Somchai Jaidee",
    "owner_kind": "person",
}


@pytest.fixture
def telegram(settings: Any, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    """Telegram in the tests: what the bot sent is kept."""
    settings.C4R_TELEGRAM_BOT_TOKEN = "1:test"  # noqa: S105 -- not a real token
    sent: list[tuple[str, dict[str, Any]]] = []

    def transport(method: str, params: dict[str, Any]) -> Any:
        sent.append((method, params))
        if method == "getMe":
            return {"username": "test_bot"}
        if method == "getFile":
            return {"file_path": "photos/1.png"}
        return True

    monkeypatch.setattr(api, "transport", transport)
    monkeypatch.setattr(api, "fetch", lambda path: PNG)
    api._name.clear()
    return sent


@pytest.fixture
def reader(settings: Any) -> Any:
    box: dict[str, Any] = {"answer": PAPER}
    recognition.register("test", lambda content, kind: box["answer"])
    settings.C4R_INVOICE_RECOGNITION = "test"
    yield box
    recognition._recognizers.pop("test", None)


_next = iter(range(1, 10_000))
SENDER = {"id": TG, "username": "anna", "language_code": "ru"}


def _update(body: dict[str, Any], secret: str | None = None) -> Any:
    return Client().post(
        "/api/telegram/webhook",
        data=json.dumps({"update_id": next(_next) + 5000, **body}),
        content_type="application/json",
        headers={"X-Telegram-Bot-Api-Secret-Token": api.secret() if secret is None else secret},
    )


def _message(**content: Any) -> Any:
    return _update({"message": {"chat": {"id": TG, "type": "private"}, "from": SENDER, **content}})


def _press(data: str) -> Any:
    chat = {"id": TG, "type": "private"}
    return _update(
        {"callback_query": {"id": "q", "data": data, "from": SENDER, "message": {"chat": chat}}}
    )


def _texts(sent: list[tuple[str, dict[str, Any]]]) -> list[str]:
    return [params["text"] for method, params in sent if method == "sendMessage"]


def _bind(owner: Client) -> None:
    url = post(owner, "/api/me/telegram/link", {}, key()).json()["url"]
    assert url.startswith("https://t.me/test_bot?start=")
    _message(text=f"/start {url.split('=')[1]}")
    _message(contact={"user_id": TG, "phone_number": "66 81 234 5678"})


def test_the_updates_are_taken_only_from_telegram(telegram: Any) -> None:
    assert _update({"message": {}}, secret="wrong").status_code == 404  # noqa: S106
    assert _update({"message": {}}).status_code == 200


def test_a_telegram_account_is_bound_by_the_link_and_the_number(
    owner: Client, telegram: list[tuple[str, dict[str, Any]]]
) -> None:
    assert owner.get("/api/me/telegram").json() == {
        "configured": True,
        "linked": False,
        "phone": "",
        "username": "",
    }
    # Somebody unknown gets nothing but the way to the profile.
    _message(text="/start")
    assert "не привязан" in _texts(telegram)[-1]
    _message(text="/start wrong-code")
    assert "устарела" in _texts(telegram)[-1]

    url = post(owner, "/api/me/telegram/link", {}, key()).json()["url"]
    _message(text=f"/start {url.split('=')[1]}")
    asked = telegram[-1][1]
    assert asked["reply_markup"]["keyboard"][0][0]["request_contact"] is True
    # A number of another person is not taken.
    _message(contact={"user_id": 5, "phone_number": "1"})
    assert "собственный номер" in _texts(telegram)[-1]
    assert TelegramLink.objects.count() == 0
    _message(contact={"user_id": TG, "phone_number": "66 81 234 5678"})
    link = TelegramLink.objects.get()
    assert (link.telegram_user_id, link.phone, link.username) == (TG, "+66812345678", "anna")
    assert owner.get("/api/me/telegram").json()["phone"] == "+66812345678"
    assert AuditEvent.objects.filter(action="telegram.linked").count() == 1
    # The link works once.
    other = Client()
    named_user(other, "second@example.com")
    _message(text=f"/start {url.split('=')[1]}")
    assert TelegramLink.objects.count() == 1

    gone = post(owner, "/api/me/telegram/unlink", {}, key())
    assert gone.status_code == 200 and gone.json()["linked"] is False
    assert AuditEvent.objects.filter(action="telegram.unlinked").count() == 1
    _message(text="/menu")
    assert "не привязан" in _texts(telegram)[-1]


def test_a_vehicle_is_made_by_a_photo_after_the_confirmation(
    owner: Client, telegram: list[tuple[str, dict[str, Any]]], reader: Any
) -> None:
    cmp = create_company(owner).json()["company"]["public_id"]
    place = post(owner, f"/api/c/{cmp}/locations", {"name": "Phuket"}, key()).json()["location"]
    _bind(owner)
    # One company — no choice: the menu at once.
    menu = telegram[-1][1]
    assert menu["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "veh"

    _press("veh")
    assert "Пришлите фото" in _texts(telegram)[-1]
    _message(photo=[{"file_id": "small", "file_size": 10}, {"file_id": "big", "file_size": 72}])
    read = telegram[-1][1]
    assert "VIN: MR2K29F30N1234567" in read["text"]
    assert "Владелец в документе: Somchai Jaidee" in read["text"]
    assert [b["callback_data"] for b in read["reply_markup"]["inline_keyboard"][0]] == ["ok", "no"]
    # Nothing is made before the confirmation.
    assert owner.get(f"/api/c/{cmp}/vehicles").json()["vehicles"] == []

    _press("ok")
    assert "Чьё" in _texts(telegram)[-1]
    _press("rel:managed")
    vehicles = owner.get(f"/api/c/{cmp}/vehicles").json()["vehicles"]
    assert len(vehicles) == 1
    card = owner.get(f"/api/c/{cmp}/vehicles/{vehicles[0]['id']}").json()["vehicle"]
    assert card["name"] == "Zzmake Zzmodel AB 1234"
    assert card["relation"] == "managed"
    assert card["location_id"] == place["id"]
    assert card["characteristics"]["vin"] == "MR2K29F30N1234567"
    assert card["characteristics"]["color"] == "White"
    assert card["owner"]["name"] == "Somchai Jaidee"
    created = [text for text in _texts(telegram) if "ТС создано" in text]
    assert created and f"/c/{cmp}/vehicles/{vehicles[0]['id']}" in created[0]
    assert AuditEvent.objects.filter(action="file.uploaded", after__through="telegram").exists()

    # «Cancel» makes nothing.
    _press("veh")
    _message(document={"file_id": "doc", "file_size": 72})
    _press("no")
    assert len(owner.get(f"/api/c/{cmp}/vehicles").json()["vehicles"]) == 1
    # A paper nothing is read from.
    reader["answer"] = {}
    _press("veh")
    _message(photo=[{"file_id": "big", "file_size": 72}])
    assert "Не удалось прочитать" in _texts(telegram)[-1]
