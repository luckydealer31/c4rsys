"""The Telegram bot (customer's decisions of 22.11.2026): binding by the link of the profile and
the shared number, the choice of the company, a vehicle by a photo of its paper. Telegram and the
service that reads the papers are replaced by functions of the tests."""

import json
from decimal import Decimal
from typing import Any

import pytest
from django.test import Client

from c4rsys.finance import expense_drafts
from c4rsys.finance.models import ExpenseDraft
from c4rsys.fleet import recognition
from c4rsys.platform.models import AuditEvent
from c4rsys.telegram import api
from c4rsys.telegram.models import TelegramLink
from c4rsys.warehouse import recognition as invoices
from c4rsys.warehouse.models import Receipt
from tests.accounts.helpers import post
from tests.companies.helpers import create_company, hire, key, named_user
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
    box: dict[str, Any] = {"answer": PAPER, "asked": 0}

    def fake(content: bytes, kind: str) -> Any:
        box["asked"] += 1
        return box["answer"]

    recognition.register("test", fake)
    settings.C4R_INVOICE_RECOGNITION = "test"
    yield box
    recognition._recognizers.pop("test", None)


@pytest.fixture
def invoice_reader(settings: Any) -> Any:
    box: dict[str, Any] = {
        "answer": {
            "supplier_name": "auto parts co",
            "document_date": "2026-11-12",
            "document_number": "INV-77",
            "currency": "THB",
            "lines": [
                {
                    "name": "Brake pads",
                    "article": "BP-100",
                    "quantity": "2",
                    "unit_cost": "1250.50",
                },
                {"name": "Unknown thing", "article": None, "quantity": "1", "unit_cost": "5"},
                {"name": "Oil 5W30", "article": None, "quantity": None, "unit_cost": "300"},
            ],
        }
    }
    invoices.register("test", lambda content, kind: box["answer"])
    settings.C4R_INVOICE_RECOGNITION = "test"
    yield box
    invoices._recognizers.pop("test", None)


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
        {
            "callback_query": {
                "id": "q",
                "data": data,
                "from": SENDER,
                "message": {"chat": chat, "message_id": 41},
            }
        }
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

    # Whose the vehicle is, is asked first; then the photo.
    _press("veh")
    assert "Чьё" in _texts(telegram)[-1]
    # The message whose button was pressed is taken away from the chat.
    assert ("deleteMessage", {"chat_id": TG, "message_id": 41}) in telegram
    _press("rel:managed")
    assert "Пришлите фото" in _texts(telegram)[-1]
    _message(photo=[{"file_id": "small", "file_size": 10}, {"file_id": "big", "file_size": 72}])
    read = telegram[-1][1]
    assert "VIN: MR2K29F30N1234567" in read["text"]
    assert "Владелец в документе: Somchai Jaidee" in read["text"]
    rows = read["reply_markup"]["inline_keyboard"]
    assert [row[0].get("callback_data") for row in rows] == ["ok", None, "no"]
    # The other way: the form of a new vehicle on the site, filled from the same paper.
    assert f"/c/{cmp}/vehicles/new?relation=managed&paper=" in rows[1][0]["url"]
    paper_id = rows[1][0]["url"].split("paper=")[1]
    again = post(owner, f"/api/c/{cmp}/vehicles/recognize", {"document_id": paper_id}, key())
    assert again.status_code == 200 and again.json()["vin"] == "MR2K29F30N1234567"
    # The paper is read by the service once: the site takes what the bot has read.
    assert reader["asked"] == 1
    # Nothing is made before the confirmation.
    assert owner.get(f"/api/c/{cmp}/vehicles").json()["vehicles"] == []

    _press("ok")
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
    _press("rel:own")
    _message(document={"file_id": "doc", "file_size": 72})
    _press("no")
    assert len(owner.get(f"/api/c/{cmp}/vehicles").json()["vehicles"]) == 1
    # A paper nothing is read from.
    reader["answer"] = {}
    _press("veh")
    _press("rel:own")
    _message(photo=[{"file_id": "big", "file_size": 72}])
    assert "Не удалось прочитать" in _texts(telegram)[-1]


def test_a_document_of_a_supplier_becomes_a_draft_of_a_receipt(
    owner: Client, telegram: list[tuple[str, dict[str, Any]]], invoice_reader: Any
) -> None:
    cmp = create_company(owner, timezone="Asia/Bangkok", main_currency="THB").json()["company"][
        "public_id"
    ]
    made = post(
        owner, f"/api/c/{cmp}/locations", {"name": "Phuket", "contexts": ["warehouse"]}, key()
    ).json()["location"]
    warehouse = next(c["id"] for c in made["contexts"] if c["type"] == "warehouse")
    items = f"/api/c/{cmp}/warehouse/items"
    pads = post(owner, items, {"name": "Колодки", "unit": "piece", "article": "bp-100"}, key())
    post(owner, items, {"name": "oil 5w30", "unit": "liter"}, key())
    supplier = post(
        owner,
        f"/api/c/{cmp}/counterparties",
        {
            "kind": "company",
            "company_name": "Auto Parts Co",
            "contacts": [{"kind": "email", "value": "sales@parts.example"}],
        },
        key(),
    )
    assert supplier.status_code == 201, supplier.content
    _bind(owner)
    menu = telegram[-1][1]["reply_markup"]["inline_keyboard"]
    assert [row[0]["callback_data"] for row in menu] == ["veh", "inv", "exp"]

    _press("inv")
    assert "Склад: Phuket" in _texts(telegram)[-1]
    _message(photo=[{"file_id": "big", "file_size": 72}])
    receipt = Receipt.objects.get()
    # A draft: nothing is in the stock until it is posted on the site.
    assert receipt.state == "draft"
    assert str(receipt.context_id) == warehouse
    assert str(receipt.supplier_id) == supplier.json()["counterparty"]["id"]
    assert receipt.document_id is not None
    assert "INV-77" in receipt.comment
    lines = [(str(line.item_id), line.quantity, line.unit_cost) for line in receipt.lines.all()]
    assert lines == [(pads.json()["item"]["id"], Decimal(2), Decimal("1250.50"))]
    assert owner.get(f"/api/c/{cmp}/warehouse/stock").json()["stock"] == []
    said = "\n".join(_texts(telegram))
    assert "Строк добавлено: 1 из 3" in said
    assert f"/c/{cmp}/warehouse/receipts/{receipt.id}" in said
    assert "— Unknown thing" in said and "— Oil 5W30" in said

    # Nothing of the document is in the catalog: no draft.
    invoice_reader["answer"] = {
        **invoice_reader["answer"],
        "lines": [{"name": "X", "quantity": "1"}],
    }
    _press("inv")
    _message(photo=[{"file_id": "big", "file_size": 72}])
    assert "черновик не создан" in _texts(telegram)[-2]
    assert Receipt.objects.count() == 1


def test_a_receipt_becomes_a_draft_of_an_expense_seen_by_its_author(
    owner: Client, telegram: list[tuple[str, dict[str, Any]]], settings: Any
) -> None:
    answer = {
        "spent_on": "2026-11-20",
        "amount": "1 250,50",
        "currency": "thb",
        "seller": "PTT Station",
        "purpose": "Fuel",
    }
    expense_drafts.register("test", lambda content, kind: answer)
    settings.C4R_INVOICE_RECOGNITION = "test"
    try:
        cmp = create_company(owner, timezone="Asia/Bangkok", main_currency="THB").json()["company"][
            "public_id"
        ]
        _bind(owner)
        _press("exp")
        assert "фото чека" in _texts(telegram)[-1]
        _message(photo=[{"file_id": "big", "file_size": 72}])
    finally:
        expense_drafts._recognizers.pop("test", None)
    said = "\n".join(_texts(telegram))
    assert "Сумма: 1250.5 THB" in said and f"/c/{cmp}/finance?tab=drafts" in said
    drafts = owner.get(f"/api/c/{cmp}/expense-drafts").json()["drafts"]
    assert len(drafts) == 1
    draft = drafts[0]
    assert (draft["spent_on"], draft["amount"], draft["currency"]) == (
        "2026-11-20",
        "1250.5",
        "THB",
    )
    assert (draft["seller"], draft["purpose"]) == ("PTT Station", "Fuel")
    assert (draft["own"], draft["author"]) == (True, "Anna Petrova")
    # The author opens the photo. An employee without the right to pay out does not see the draft
    # nor its photo; an authorized one — who may pay out — sees both.
    assert owner.get(f"/api/files/{draft['file_id']}").status_code == 200
    other, _ = hire(owner, cmp, "second@example.com", [{"type": "set", "set": "driver"}])
    assert other.get(f"/api/c/{cmp}/expense-drafts").json()["drafts"] == []
    assert other.get(f"/api/files/{draft['file_id']}").status_code == 404
    account = post(
        owner,
        f"/api/c/{cmp}/cash-accounts",
        {"name": "Cash", "type": "cash", "currency": "THB"},
        key(),
    )
    assert account.status_code == 201, account.content
    articles = owner.get(f"/api/c/{cmp}/finance-articles").json()["articles"]
    article = next(a for a in articles if a["kind"] == "expense")
    body = {
        "cash_account_id": account.json()["cash_account"]["id"],
        "amount": "1250.50",
        "currency": "THB",
        "article_id": article["id"],
        "note": "PTT Station — Fuel",
        "spent_on": "2026-11-20",
    }
    refused = post(other, f"/api/c/{cmp}/expense-drafts/{draft['id']}/record", body, key())
    assert refused.status_code in (403, 404)
    future = post(
        owner,
        f"/api/c/{cmp}/expense-drafts/{draft['id']}/record",
        {**body, "spent_on": "2999-01-01"},
        key(),
    )
    assert future.status_code == 400
    done = post(owner, f"/api/c/{cmp}/expense-drafts/{draft['id']}/record", body, key())
    assert done.status_code == 201, done.content
    assert done.json()["record"]["direction"] == "out"
    assert owner.get(f"/api/c/{cmp}/expense-drafts").json()["drafts"] == []
    kept = ExpenseDraft.objects.get()
    assert kept.state == "recorded" and kept.movement is not None
    # The expense is recorded by the date of the receipt: its noon in the time zone of the company.
    assert str(kept.movement.posted_at) == "2026-11-20 05:00:00+00:00"
    # A closed draft is not recorded twice.
    again = post(owner, f"/api/c/{cmp}/expense-drafts/{draft['id']}/record", body, key())
    assert again.status_code == 422
