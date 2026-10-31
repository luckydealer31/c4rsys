"""Block 12, decisions of the customer of 31.10.2026 (BLOCK12_PLAN «Решения заказчика»):

* В-12.1 — two cards of one company are merged only after the verification found one vehicle;
* В-12.2 — the unconfirmed characteristics of a shared vehicle are changed by its owner only; the
  other companies keep their values until they apply the owner's data;
* В-12.3 — one vehicle in the own or managed fleet of two companies: the cards are linked and a
  dispute of ownership or management is opened for the verification;
* В-12.4 — no link of a card to a vehicle without a match of identifiers.
"""

from typing import Any

import pytest
from django.test import Client
from django.urls import Resolver404, resolve
from django.utils import timezone

from c4rsys.backoffice.models import SystemJournalEntry
from c4rsys.fleet import shared
from c4rsys.fleet.models import (
    IdentifierConflict,
    Vehicle,
    VehicleCard,
    VehicleCardWorkingValue,
    VehicleOperationalDispute,
    VehicleOwnership,
)
from c4rsys.notifications.models import Notification
from c4rsys.platform.models import AuditEvent
from tests.accounts.helpers import post
from tests.backoffice.helpers import Member, err, member, sp
from tests.backoffice.test_vehicle_identity import VIN, card, create, details
from tests.backoffice.test_vehicle_identity import company as new_company
from tests.companies.helpers import key

pytestmark = pytest.mark.django_db


@pytest.fixture
def verifier() -> Member:
    return member("verify@c4rsys.test", ["verification"], unit="Verification")


@pytest.fixture
def two(owner: Client) -> dict[str, str]:
    a, la = new_company(owner, "Alpha Rent", "c-a")
    b, lb = new_company(owner, "Beta Service", "c-b")
    return {"a": a, "la": la, "b": b, "lb": lb}


def same_vehicle(verifier: Member, primary: str) -> Any:
    conflict = IdentifierConflict.objects.get(is_open=True)
    return sp(
        verifier,
        f"vehicle-conflicts/{conflict.id}/decide",
        {"outcome": "same_vehicle", "reason": "Same VIN and papers", "primary": primary},
    )


def apply(owner: Client, cmp: str, vehicle: dict[str, Any]) -> Any:
    return post(
        owner,
        f"/api/c/{cmp}/vehicles/{vehicle['id']}/owner-data",
        {"expected_version": vehicle["version"]},
        key(),
    )


@pytest.fixture
def linked(owner: Client, two: dict[str, str], verifier: Member) -> dict[str, Any]:
    """A rental fleet (own card) and a service company (client card) of one vehicle."""
    first = create(owner, two["a"], two["la"], vin=VIN, year=2019, color="White").json()
    second = create(owner, two["b"], two["lb"], vin=VIN, relation="client").json()
    assert same_vehicle(verifier, first["vehicle"]["vehicle_id"]).status_code == 200
    return {**two, "first": first["vehicle"], "second": second["vehicle"]}


# --- В-12.2: the owner changes the shared values -------------------------------------------------


def test_a_company_that_is_not_the_owner_changes_its_card_only(
    owner: Client, linked: dict[str, Any]
) -> None:
    b, a = linked["b"], linked["a"]
    second = card(owner, b, linked["second"]["id"])
    # The service company is not the owner: the only own card is of the company A.
    assert second["changes_global"] is False
    assert card(owner, a, linked["first"]["id"])["changes_global"] is True

    response = details(owner, b, second, color="Red")

    assert response.status_code == 200, response.content
    shown = response.json()["vehicle"]
    assert shown["working_values"]["color"]["value"] == "Red"
    assert shown["working_values"]["color"]["origin"] == "card"
    assert shown["working_values"]["color"]["owner_newer"] is False
    # The global value and the owner's card do not change.
    assert Vehicle.objects.get(public_id=linked["first"]["vehicle_id"]).color == "White"
    assert card(owner, a, linked["first"]["id"])["working_values"] == {}
    # The earlier and the new working value are in the journal of the card.
    changed = AuditEvent.objects.filter(action="vehicle.details_changed").latest("id")
    assert changed.after is not None and changed.after["working_values"] == {"color": "Red"}


def test_the_owner_change_waits_for_the_other_company(
    owner: Client, linked: dict[str, Any]
) -> None:
    a, b = linked["a"], linked["b"]
    first = card(owner, a, linked["first"]["id"])

    assert details(owner, a, first, year=2021).status_code == 200

    main = Vehicle.objects.get(public_id=linked["first"]["vehicle_id"])
    assert main.year == 2021
    # The other card keeps what it showed, with the mark that the owner's data are newer.
    second = card(owner, b, linked["second"]["id"])
    assert second["owner_data_newer"] == ["year"]
    year = second["working_values"]["year"]
    assert (year["value"], year["origin"], year["owner_newer"]) == (2019, "previous", True)
    assert year["global"]["value"] == 2021
    # The owner's card shows the new value.
    assert card(owner, a, linked["first"]["id"])["working_values"] == {}

    applied = apply(owner, b, second)

    assert applied.status_code == 200, applied.content
    after = applied.json()["vehicle"]
    assert after["owner_data_newer"] == [] and after["working_values"] == {}
    assert after["characteristics"]["year"] == 2021
    row = VehicleCardWorkingValue.objects.get(vehicle_card_id=second["id"], field="year")
    assert row.ended_at is not None and row.ended_reason == "applied"
    event = AuditEvent.objects.get(action="vehicle.owner_data_applied")
    assert event.before == {"working_values": {"year": 2019}}
    assert event.after == {"values": {"year": 2021}}
    # Nothing newer is left.
    assert err(apply(owner, b, after)) == "VEHICLE_OWNER_DATA_NOT_NEWER"


def test_the_own_value_of_a_card_gets_the_mark_too(owner: Client, linked: dict[str, Any]) -> None:
    a, b = linked["a"], linked["b"]
    assert details(owner, b, card(owner, b, linked["second"]["id"]), color="Red").status_code == 200

    assert (
        details(owner, a, card(owner, a, linked["first"]["id"]), color="Black").status_code == 200
    )

    second = card(owner, b, linked["second"]["id"])
    color = second["working_values"]["color"]
    assert (color["value"], color["owner_newer"], color["global"]["value"]) == (
        "Red",
        True,
        "Black",
    )
    assert apply(owner, b, second).json()["vehicle"]["working_values"] == {}


def test_the_confirmed_owner_is_the_owner(owner: Client, linked: dict[str, Any]) -> None:
    """A person confirmed as the owner: no company changes the shared values (the person's
    edit in the personal cabinet is a TODO(spec))."""
    a = linked["a"]
    main = Vehicle.objects.get(public_id=linked["first"]["vehicle_id"])
    person = VehicleCard.objects.get(pk=linked["first"]["id"]).created_by_user_id
    VehicleOwnership.objects.create(vehicle=main, owner_user_id=person, started_at=timezone.now())
    first = card(owner, a, linked["first"]["id"])
    assert first["changes_global"] is False

    assert details(owner, a, first, year=2022).status_code == 200

    main.refresh_from_db()
    assert main.year == 2019
    assert card(owner, a, linked["first"]["id"])["working_values"]["year"]["value"] == 2022


def test_without_an_owner_every_card_keeps_its_value(
    owner: Client, two: dict[str, str], verifier: Member
) -> None:
    """No confirmed owner and no own card: nobody changes the shared value (OPEN)."""
    first = create(
        owner,
        two["a"],
        two["la"],
        vin=VIN,
        year=2019,
        relation="managed",
    ).json()
    assert "vehicle" in first, first
    second = create(owner, two["b"], two["lb"], vin=VIN, relation="client").json()["vehicle"]
    assert same_vehicle(verifier, first["vehicle"]["vehicle_id"]).status_code == 200
    for cmp, found in ((two["a"], first["vehicle"]), (two["b"], second)):
        shown = card(owner, cmp, found["id"])
        assert shown["changes_global"] is False
        assert details(owner, cmp, shown, year=2020).status_code == 200
    main = Vehicle.objects.get(public_id=first["vehicle"]["vehicle_id"])
    assert main.year == 2019


def test_a_vehicle_of_one_company_is_not_shared(owner: Client, two: dict[str, str]) -> None:
    vehicle = create(owner, two["a"], two["la"], relation="client", year=2019).json()["vehicle"]
    assert vehicle["changes_global"] is True
    assert details(owner, two["a"], vehicle, year=2020).status_code == 200
    assert Vehicle.objects.get(public_id=vehicle["vehicle_id"]).year == 2020


def test_documents_take_the_values_of_the_card(owner: Client, linked: dict[str, Any]) -> None:
    b = linked["b"]
    assert details(owner, b, card(owner, b, linked["second"]["id"]), color="Red").status_code == 200
    found = VehicleCard.objects.get(pk=linked["second"]["id"])
    assert shared.card_values(found)["color"] == "Red"
    assert shared.card_values(VehicleCard.objects.get(pk=linked["first"]["id"]))["color"] == (
        "White"
    )


def test_an_archived_card_does_not_apply(owner: Client, linked: dict[str, Any]) -> None:
    a, b = linked["a"], linked["b"]
    assert details(owner, a, card(owner, a, linked["first"]["id"]), year=2021).status_code == 200
    VehicleCard.objects.filter(pk=linked["second"]["id"]).update(state="archived")
    assert err(apply(owner, b, card(owner, b, linked["second"]["id"]))) == "VEHICLE_ARCHIVED"


# --- В-12.3: one vehicle in the fleets of two companies ------------------------------------------


def test_two_operational_records_are_linked_with_a_dispute(
    owner: Client, two: dict[str, str], verifier: Member
) -> None:
    first = create(owner, two["a"], two["la"], vin=VIN).json()["vehicle"]
    second = create(owner, two["b"], two["lb"], vin=VIN).json()["vehicle"]

    response = same_vehicle(verifier, first["vehicle_id"])

    assert response.status_code == 200, response.content
    conflict = IdentifierConflict.objects.get()
    assert (conflict.is_open, conflict.review) == (False, "same_vehicle")
    main = Vehicle.objects.get(public_id=first["vehicle_id"])
    assert VehicleCard.objects.get(pk=second["id"]).vehicle_id == main.id
    dispute = VehicleOperationalDispute.objects.get()
    assert dispute.is_open and dispute.vehicle_id == main.id
    assert dispute.parties.count() == 2
    [shown] = response.json()["conflict"]["operational_disputes"]
    assert {p["company"]["name"] for p in shown["parties"]} == {"Alpha Rent", "Beta Service"}

    # Each company is told the number of the conflict only.
    told = Notification.objects.filter(event="vehicle.operational_dispute_opened")
    assert told.count() == 2
    assert all(n.params == {"number": conflict.number} for n in told)
    text = str(card(owner, two["b"], second["id"]))
    assert "Alpha" not in text and two["a"] not in text
    assert card(owner, two["b"], second["id"])["operational_disputes"][0]["number"] == (
        conflict.number
    )

    # The verification sees the dispute in its queue.
    queue = verifier.client.get("/api/staff/verification").json()
    assert [d["id"] for d in queue["operational_disputes"]] == [str(dispute.id)]

    # While it is open both cards keep working (the ТЗ is silent: OPEN).
    for cmp, found in ((two["a"], first), (two["b"], second)):
        assert details(owner, cmp, card(owner, cmp, found["id"]), color="Grey").status_code == 200

    closed = sp(
        verifier,
        f"vehicle-disputes/{dispute.id}/close",
        {"reason": "Company B returned the vehicle", "evidence": "Contract"},
    )

    assert closed.status_code == 200, closed.content
    dispute.refresh_from_db()
    assert not dispute.is_open and dispute.reason == "Company B returned the vehicle"
    assert SystemJournalEntry.objects.filter(
        action="verification.operational_dispute_decided"
    ).exists()
    assert Notification.objects.filter(event="vehicle.operational_dispute_decided").count() == 2
    again = sp(verifier, f"vehicle-disputes/{dispute.id}/close", {"reason": "x"})
    assert err(again) == "DISPUTE_NOT_OPEN"


def test_only_the_verification_closes_a_dispute(
    owner: Client, two: dict[str, str], verifier: Member, support: Member
) -> None:
    first = create(owner, two["a"], two["la"], vin=VIN).json()["vehicle"]
    create(owner, two["b"], two["lb"], vin=VIN, relation="managed")
    assert same_vehicle(verifier, first["vehicle_id"]).status_code == 200
    dispute = VehicleOperationalDispute.objects.get()
    denied = sp(support, f"vehicle-disputes/{dispute.id}/close", {"reason": "x"})
    assert denied.status_code == 403


def test_a_client_card_does_not_open_a_dispute(owner: Client, linked: dict[str, Any]) -> None:
    assert not VehicleOperationalDispute.objects.exists()


# --- В-12.1, В-12.4 ---------------------------------------------------------------------------


def test_two_cards_of_a_company_are_merged_only_after_the_verification(
    owner: Client, two: dict[str, str], verifier: Member
) -> None:
    cmp, location = two["a"], two["la"]
    first = create(owner, cmp, location, vin=VIN, name="Yaris 1").json()["vehicle"]
    second = create(owner, cmp, location, vin=VIN, name="Yaris 2").json()["vehicle"]
    url = f"/api/c/{cmp}/vehicles/{first['id']}/merge/preview?duplicate={second['id']}"
    assert "different_entities" in owner.get(url).json()["comparison"]["obstacles"]
    assert same_vehicle(verifier, first["vehicle_id"]).status_code == 200
    assert "different_entities" not in owner.get(url).json()["comparison"]["obstacles"]


def test_there_is_no_manual_link_of_a_card_to_a_vehicle() -> None:
    """В-12.4: a card is linked to another global vehicle only by a decision on a conflict."""
    for path in ("/api/staff/vehicles/VHL-X/link", "/api/staff/vehicle-cards/link"):
        with pytest.raises(Resolver404):
            resolve(path)
