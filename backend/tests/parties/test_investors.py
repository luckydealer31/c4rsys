"""The investors: a list of its own, apart from the counterparties and the clients (ТЗ § 6.1.7,
§ 8.3.13, § 23.8.2; customer's decisions of 26.11.2026)."""

import importlib
from types import SimpleNamespace
from typing import Any

import pytest
from django.apps import apps
from django.db import connection
from django.test import Client

from c4rsys.fleet.models import VehicleOwnerClaim
from c4rsys.parties.models import ClientCard, Counterparty
from c4rsys.warehouse.models import PurchaseOrder
from tests.accounts.helpers import post
from tests.companies.helpers import create_company, hire, key

pytestmark = pytest.mark.django_db

PHONE = [{"kind": "phone", "value": "+66 81 234 5678"}]


@pytest.fixture
def cmp(owner: Client) -> str:
    public_id: str = create_company(owner).json()["company"]["public_id"]
    return public_id


def create(client: Client, cmp: str, list_name: str = "investors", **data: Any) -> Any:
    body = {"kind": "company", "company_name": "Phuket Capital", "contacts": PHONE, **data}
    return post(client, f"/api/c/{cmp}/{list_name}", body, key())


def grant(*permissions: str) -> list[dict[str, str]]:
    return [
        {
            "type": "grant",
            "permission": permission,
            "locations": "all",
            "contexts": "all",
            "cash_accounts": "all",
        }
        for permission in permissions
    ]


def test_the_investors_are_a_list_apart(owner: Client, cmp: str) -> None:
    created = create(owner, cmp)
    assert created.status_code == 201, created.content
    card = created.json()["counterparty"]
    assert card["role"] == "investor" and card["name"] == "Phuket Capital"
    # Not a counterparty and not a client.
    assert owner.get(f"/api/c/{cmp}/counterparties").json()["counterparties"] == []
    assert not ClientCard.objects.exists()
    found = owner.get(f"/api/c/{cmp}/investors?q=capital").json()["counterparties"]
    assert [c["id"] for c in found] == [card["id"]]
    # The card is opened, changed and archived by the address of its list only.
    assert owner.get(f"/api/c/{cmp}/investors/{card['id']}").status_code == 200
    assert owner.get(f"/api/c/{cmp}/counterparties/{card['id']}").status_code == 404
    wrong = post(
        owner, f"/api/c/{cmp}/counterparties/{card['id']}/archive", {"expected_version": 1}, key()
    )
    assert wrong.status_code == 404
    archived = post(
        owner, f"/api/c/{cmp}/investors/{card['id']}/archive", {"expected_version": 1}, key()
    )
    assert archived.json()["counterparty"]["state"] == "archived"

    # One who put in money is entered by hand, as a person too.
    person = create(owner, cmp, kind="person", company_name="", first_name="Somchai", last_name="K")
    assert person.status_code == 201, person.content


def test_the_same_party_is_a_counterparty_and_an_investor_apart(owner: Client, cmp: str) -> None:
    """The same phone in the two lists is not a duplicate: the lists are kept apart."""
    investor = create(owner, cmp).json()["counterparty"]
    supplier = create(owner, cmp, "counterparties").json()["counterparty"]
    assert supplier["role"] == "counterparty" and supplier["id"] != investor["id"]
    body = {"phone": "+66 81 234 5678"}
    as_investor = post(owner, f"/api/c/{cmp}/investors/matches", body, key()).json()["matches"]
    as_supplier = post(owner, f"/api/c/{cmp}/counterparties/matches", body, key()).json()["matches"]
    assert [m["counterparty"]["id"] for m in as_investor] == [investor["id"]]
    assert [m["counterparty"]["id"] for m in as_supplier] == [supplier["id"]]
    # The cards of different lists are not merged.
    url = (
        f"/api/c/{cmp}/card-merges/preview?card_type=investor"
        f"&primary_id={investor['id']}&duplicate_id={supplier['id']}"
    )
    assert owner.get(url).status_code == 404


def test_the_rights_of_the_investors_are_their_own(owner: Client, cmp: str) -> None:
    """The list is open not to everybody: the rights of the counterparties do not open it."""
    card = create(owner, cmp).json()["counterparty"]
    supplier, _ = hire(
        owner, cmp, "supplier@example.com", grant("counterparty.view", "counterparty.edit")
    )
    assert supplier.get(f"/api/c/{cmp}/investors").status_code == 403
    assert supplier.get(f"/api/c/{cmp}/investors/{card['id']}").status_code == 403
    assert supplier.get(f"/api/c/{cmp}/investors/{card['id']}/vehicles").status_code == 403
    assert supplier.get(f"/api/c/{cmp}/counterparties/{card['id']}").status_code == 404
    links = f"/api/c/{cmp}/profile-links?card_id={card['id']}&card_type="
    assert supplier.get(links + "counterparty").status_code == 404
    assert supplier.get(links + "investor").status_code == 403
    merges = f"/api/c/{cmp}/card-merges?card_id={card['id']}&card_type="
    assert supplier.get(merges + "counterparty").status_code == 404
    assert supplier.get(merges + "investor").status_code == 403

    viewer, _ = hire(owner, cmp, "viewer@example.com", grant("investor.view"))
    assert viewer.get(f"/api/c/{cmp}/investors").status_code == 200
    assert viewer.get(links + "investor").status_code == 200
    assert create(viewer, cmp, company_name="Other").status_code == 403
    assert viewer.get(f"/api/c/{cmp}/counterparties").status_code == 403


def test_the_owners_named_as_counterparties_become_investors(
    owner: Client, cmp: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The migration: an owner of a vehicle passes to the investors; a card that is also used
    as a counterparty stays one, and an investor card with its data is made beside it."""
    location = post(owner, f"/api/c/{cmp}/locations", {"name": "Phuket"}, key()).json()["location"]

    def vehicle(vin: str, card_id: str) -> str:
        body = {
            "location_id": location["id"],
            "odometer_unit": "km",
            "name": "Yaris",
            "vin": vin,
            "relation": "managed",
            "owner_kind": "counterparty",
            "owner_counterparty_id": card_id,
        }
        created = post(owner, f"/api/c/{cmp}/vehicles", body, key())
        assert created.status_code == 201, created.content
        return str(created.json()["vehicle"]["id"])

    plain = create(owner, cmp, company_name="Only Owner").json()["counterparty"]
    busy = create(owner, cmp, company_name="Owner And Supplier").json()["counterparty"]
    first = vehicle("JTDBT923771234567", plain["id"])
    second = vehicle("JTDBT923771234568", busy["id"])
    # As it was before the investors: both are counterparties, one of them a supplier too.
    Counterparty.objects.filter(pk__in=[plain["id"], busy["id"]]).update(role="counterparty")
    migration = importlib.import_module("c4rsys.fleet.migrations.0034_owners_to_investors")
    # The uses of a card as a counterparty are found by the references of the tables.
    with connection.cursor() as cursor:
        cursor.execute(migration.REFERENCES)
        references = {(table.strip('"'), column) for table, column in cursor.fetchall()}
    assert (PurchaseOrder._meta.db_table, "supplier_id") in references
    assert migration.OWN <= references
    monkeypatch.setattr(
        migration, "used_elsewhere", lambda cursor, refs, card_id: str(card_id) == busy["id"]
    )
    migration.forward(apps, SimpleNamespace(connection=connection))

    assert Counterparty.objects.get(pk=plain["id"]).role == "investor"
    assert Counterparty.objects.get(pk=busy["id"]).role == "counterparty"
    copy = Counterparty.objects.exclude(pk__in=[plain["id"], busy["id"]]).get()
    assert (copy.role, copy.company_name) == ("investor", "Owner And Supplier")
    assert [c.value for c in copy.contacts.all()] == ["+66 81 234 5678"]

    def owner_of(card_id: str) -> Any:
        claim = VehicleOwnerClaim.objects.filter(vehicle_card__pk=card_id).latest("created_at")
        return claim.counterparty_id

    assert str(owner_of(first)) == plain["id"] and owner_of(second) == copy.id
    shown = owner.get(f"/api/c/{cmp}/investors/{copy.id}/vehicles").json()
    assert [v["id"] for v in shown["vehicles"]] == [second] and shown["count"] == 1
