"""A new card filled from a photo of a document (customer's decisions of 28.11.2026)."""

from collections.abc import Iterator
from typing import Any

import pytest
from django.test import Client

from c4rsys.parties import recognition
from c4rsys.parties.models import ClientCard, Counterparty
from tests.accounts.helpers import post
from tests.companies.helpers import create_company, hire, key
from tests.files.helpers import upload

pytestmark = pytest.mark.django_db

PHONE = [{"kind": "phone", "value": "+66 81 234 5678"}]
PASSPORT = {
    "party_kind": "person",
    "document_kind": "passport",
    "last_name": "SHCHUKIN",
    "first_name": "TIMOFEI",
    "middle_name": "",
    "birth_date": "1990-02-03",
    "number": "75 1234567",
    "country": "ru",
    "issued_on": "2020-05-06",
    "expires_on": "2030-05-06",
    "issued_by": "MVD 77001",
    "categories": "",
    "address": "",
    "company_name": "",
    "legal_form": "",
    "registration_number": "",
    "tax_number": "",
    "director_name": "",
}


@pytest.fixture
def cmp(owner: Client) -> str:
    public_id: str = create_company(owner).json()["company"]["public_id"]
    return public_id


@pytest.fixture
def reader(settings: Any) -> Iterator[dict[str, Any]]:
    box: dict[str, Any] = {"answer": PASSPORT}
    recognition.register("test", lambda content, kind: box["answer"])
    settings.C4R_INVOICE_RECOGNITION = "test"
    yield box
    recognition._recognizers.pop("test", None)


def test_a_passport_fills_a_new_client_and_its_photo_is_kept(
    owner: Client, cmp: str, reader: dict[str, Any]
) -> None:
    url = f"/api/c/{cmp}/cards/recognize"
    assert owner.get(url).json() == {"available": True}
    paper = upload(owner, "party_paper").json()["file"]["id"]

    read = post(owner, url, {"document_id": paper, "card_type": "client"}, key())

    assert read.status_code == 200, read.content
    body = read.json()
    assert (body["kind"], body["document_kind"]) == ("person", "passport")
    card = body["card"]
    assert (card["last_name"], card["first_name"], card["birth_date"]) == (
        "SHCHUKIN",
        "TIMOFEI",
        "1990-02-03",
    )
    assert card["id_document"] == {
        "type": "passport",
        "number": "75 1234567",
        "country": "RU",
        "issued_on": "2020-05-06",
        "expires_on": "2030-05-06",
        "issued_by": "MVD 77001",
    }
    # Nothing is written by the reading: the card is made by the employee.
    assert ClientCard.objects.count() == 0
    made = post(
        owner,
        f"/api/c/{cmp}/clients",
        {
            "kind": "person",
            **{k: v for k, v in card.items() if v},
            "contacts": PHONE,
            "paper": {"file_id": paper, "kind": body["document_kind"]},
        },
        key(),
    )
    assert made.status_code == 201, made.content
    client = made.json()["client"]
    assert client["id_document"]["number"] == "75 1234567"
    assert [(p["file_id"], p["kind"]) for p in client["papers"]] == [(paper, "passport")]
    # The photo is read through the card, by who may see the card.
    file_url = f"/api/c/{cmp}/clients/{client['id']}/papers/{paper}"
    assert owner.get(file_url).status_code == 200
    stranger, _ = hire(owner, cmp, "stranger@example.com", [])
    assert stranger.get(file_url).status_code == 403
    other = upload(owner, "party_paper", key="f-2").json()["file"]["id"]
    assert owner.get(f"/api/c/{cmp}/clients/{client['id']}/papers/{other}").status_code == 404


def test_a_licence_and_a_company_paper(owner: Client, cmp: str, reader: dict[str, Any]) -> None:
    url = f"/api/c/{cmp}/cards/recognize"
    paper = upload(owner, "party_paper").json()["file"]["id"]
    # A licence; what cannot be a value is dropped: a date of the future, an unknown country.
    reader["answer"] = {
        **PASSPORT,
        "document_kind": "driver_license",
        "categories": "A, B",
        "issued_on": "2999-01-01",
        "country": "Russia",
    }
    card = post(owner, url, {"document_id": paper, "card_type": "client"}, key()).json()["card"]
    assert "id_document" not in card
    assert card["driver_license"] == {
        "number": "75 1234567",
        "country": "",
        "issued_on": None,
        "expires_on": "2030-05-06",
        "issued_by": "MVD 77001",
        "categories": ["A", "B"],
    }
    # A company, for an investor: the paper is kept with the card of the list.
    reader["answer"] = {
        **{k: "" for k in PASSPORT},
        "party_kind": "company",
        "document_kind": "company_registration",
        "company_name": "Phuket Capital",
        "legal_form": "Co., Ltd.",
        "registration_number": "0835561012345",
        "country": "TH",
        "address": "Phuket, Rawai 5",
    }
    read = post(owner, url, {"document_id": paper, "card_type": "investor"}, key()).json()
    assert read["kind"] == "company"
    assert read["card"] == {
        "company_name": "Phuket Capital",
        "legal_form": "Co., Ltd.",
        "registration_number": "0835561012345",
        "tax_number": "",
        "director_name": "",
        "legal_address": "Phuket, Rawai 5",
        "country": "TH",
    }
    made = post(
        owner,
        f"/api/c/{cmp}/investors",
        {
            "kind": "company",
            **read["card"],
            "contacts": PHONE,
            "paper": {"file_id": paper, "kind": read["document_kind"]},
        },
        key(),
    )
    assert made.status_code == 201, made.content
    investor = made.json()["counterparty"]
    assert investor["registration_number"] == "0835561012345"
    assert [p["kind"] for p in investor["papers"]] == ["company_registration"]
    assert Counterparty.objects.get(pk=investor["id"]).role == "investor"
    file_url = f"/api/c/{cmp}/investors/{investor['id']}/papers/{paper}"
    assert owner.get(file_url).status_code == 200
    # Not through another list.
    assert owner.get(file_url.replace("investors", "counterparties")).status_code == 404


def test_the_reading_needs_the_right_to_create_and_its_own_file(
    owner: Client, cmp: str, reader: dict[str, Any]
) -> None:
    url = f"/api/c/{cmp}/cards/recognize"
    paper = upload(owner, "party_paper").json()["file"]["id"]
    viewer, _ = hire(owner, cmp, "viewer@example.com", [])
    assert (
        post(viewer, url, {"document_id": paper, "card_type": "client"}, key()).status_code == 403
    )
    wrong = post(owner, url, {"document_id": paper, "card_type": "vehicle"}, key())
    assert wrong.json()["error"]["code"] == "VALUE_UNKNOWN"
    # A file of another purpose is not read as a document of a card.
    photo = upload(owner, "vehicle_paper", key="f-3").json()["file"]["id"]
    refused = post(owner, url, {"document_id": photo, "card_type": "client"}, key())
    assert refused.json()["error"]["code"] == "FILE_NOT_USABLE"


def test_without_a_service_the_reading_is_off(owner: Client, cmp: str, settings: Any) -> None:
    settings.C4R_INVOICE_RECOGNITION = ""
    assert owner.get(f"/api/c/{cmp}/cards/recognize").json() == {"available": False}
