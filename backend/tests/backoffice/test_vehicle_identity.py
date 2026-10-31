"""Block 12 part А: identity of vehicles — conflicts of identifiers, one global entity, merge and
split of cards, confirmed characteristics and the tools of the c4rsys staff (ТЗ § 8.1.11,
§ 8.2.1, § 8.2.8, § 8.3.2, § 8.3.16, § 8.6.1–8.6.6, § 24.1.5)."""

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.test import Client
from django.utils import timezone

from c4rsys.backoffice.models import SystemJournalEntry
from c4rsys.fleet import services
from c4rsys.fleet.models import (
    IdentifierConflict,
    IdentifierConflictDecision,
    IdentifierConflictParty,
    OdometerReading,
    Vehicle,
    VehicleCard,
    VehicleCardMerge,
    VehicleCharacteristicEvent,
    VehicleMake,
    VehicleOwnership,
    VehicleOwnershipRevision,
)
from c4rsys.notifications.models import Notification
from c4rsys.platform.commands import Actor, CommandContext
from c4rsys.platform.errors import ApiError
from c4rsys.platform.models import AuditEvent
from tests.accounts.helpers import CODE, post
from tests.backoffice.helpers import Member, err, member, sp
from tests.companies.helpers import create_company, hire, key

pytestmark = pytest.mark.django_db

VIN = "JTDBR32E720123456"


@pytest.fixture
def verifier() -> Member:
    return member("verify@c4rsys.test", ["verification"], unit="Verification")


def company(owner: Client, name: str, k: str) -> tuple[str, str]:
    cmp: str = create_company(owner, k, display_name=name, timezone="Asia/Bangkok").json()[
        "company"
    ]["public_id"]
    location = post(owner, f"/api/c/{cmp}/locations", {"name": f"{name} base"}, key())
    return cmp, location.json()["location"]["id"]


@pytest.fixture
def two(owner: Client) -> dict[str, str]:
    a, la = company(owner, "Alpha Rent", "c-a")
    b, lb = company(owner, "Beta Service", "c-b")
    return {"a": a, "la": la, "b": b, "lb": lb}


def create(owner: Client, cmp: str, location: str, **data: Any) -> Any:
    body = {"name": "Toyota Yaris", "location_id": location, "odometer_unit": "km", **data}
    return post(owner, f"/api/c/{cmp}/vehicles", body, key())


def card(owner: Client, cmp: str, card_id: str) -> dict[str, Any]:
    found: dict[str, Any] = owner.get(f"/api/c/{cmp}/vehicles/{card_id}").json()["vehicle"]
    return found


def details(owner: Client, cmp: str, vehicle: dict[str, Any], **changes: Any) -> Any:
    c = vehicle["characteristics"]
    body = {
        "expected_version": vehicle["version"],
        "name": vehicle["name"],
        "vin": c["vin"],
        "plate": c["plate"],
        "plate_country": c["plate_country"],
        "year": c["year"],
        "body_number": c["body_number"],
        "color": c["color"],
        **changes,
    }
    return owner.put(
        f"/api/c/{cmp}/vehicles/{vehicle['id']}/details",
        data=body,
        content_type="application/json",
        headers=key(),
    )


# --- Detection and what a company sees (§ 8.6.1, § 8.6.3) --------------------------------------


def test_a_vin_match_is_recorded_and_does_not_block(owner: Client, two: dict[str, str]) -> None:
    first = create(owner, two["a"], two["la"], vin=VIN)
    # The VIN is upper-cased and trimmed; the original value is kept.
    second = create(owner, two["b"], two["lb"], vin=f"  {VIN.lower()} ", relation="client")
    assert first.status_code == 201 and second.status_code == 201, second.content

    conflict = IdentifierConflict.objects.get()
    assert (conflict.kind, conflict.normalized_value, conflict.is_open) == ("vin", VIN, True)
    parties = IdentifierConflictParty.objects.filter(conflict=conflict)
    assert {p.original_value for p in parties} == {VIN, f"  {VIN.lower()} "}
    assert {p.normalized_value for p in parties} == {VIN}

    shown = second.json()["vehicle"]
    assert shown["identifier_conflict_kinds"] == ["vin"]
    [mine] = shown["identifier_conflicts"]
    assert mine["number"] == conflict.number and mine["own_cards"] == []
    # Only the fact and the number: nothing of the other company.
    text = str(card(owner, two["b"], shown["id"]))
    assert "Alpha" not in text and two["a"] not in text
    assert first.json()["vehicle"]["vehicle_id"] not in text


def test_similar_symbols_are_not_equal(owner: Client, two: dict[str, str]) -> None:
    create(owner, two["a"], two["la"], vin="JTDBR32E72O123456")
    create(owner, two["b"], two["lb"], vin="JTDBR32E720123456")
    assert not IdentifierConflict.objects.exists()


def test_a_plate_matches_within_its_country_and_region(owner: Client, two: dict[str, str]) -> None:
    create(owner, two["a"], two["la"], plate="กข 1234", plate_country="TH", plate_region="Phuket")
    create(owner, two["b"], two["lb"], plate="กข-1234", plate_country="RU")
    assert not IdentifierConflict.objects.exists()
    create(owner, two["b"], two["lb"], plate="กข1234", plate_country="TH", plate_region="Bangkok")
    assert not IdentifierConflict.objects.exists()
    create(owner, two["b"], two["lb"], plate="กข 1234", plate_country="TH")
    conflict = IdentifierConflict.objects.get()
    assert (conflict.kind, conflict.country) == ("plate", "TH")


def test_body_numbers_of_other_countries_do_not_match(owner: Client, two: dict[str, str]) -> None:
    create(owner, two["a"], two["la"], body_number="KZJ95-0001", plate="A1", plate_country="TH")
    create(owner, two["b"], two["lb"], body_number="kzj95-0001", plate="B2", plate_country="RU")
    assert not IdentifierConflict.objects.exists()
    create(owner, two["b"], two["lb"], body_number="KZJ95-0001")
    assert IdentifierConflict.objects.get().kind == "body_number"


def test_a_corrected_value_removes_the_cause(owner: Client, two: dict[str, str]) -> None:
    create(owner, two["a"], two["la"], vin=VIN)
    second = create(owner, two["b"], two["lb"], vin=VIN).json()["vehicle"]

    response = details(owner, two["b"], second, vin="JTDBR32E720999999")

    assert response.status_code == 200, response.content
    conflict = IdentifierConflict.objects.get()
    assert (conflict.is_open, conflict.review) == (False, "cause_removed")
    decision = IdentifierConflictDecision.objects.get(conflict=conflict)
    assert decision.outcome == "cause_removed" and decision.decided_by_user_id is None
    # The previous value stays in the history of the card.
    changed = AuditEvent.objects.filter(action="vehicle.details_changed").latest("id")
    assert changed.before is not None and changed.before["vin"] == VIN
    left = IdentifierConflictParty.objects.get(vehicle_card_id=second["id"])
    assert left.left_at is not None


# --- Decisions of the verification (§ 8.6.2, § 24.1.5) ------------------------------------------


def _conflict(verifier: Member) -> dict[str, Any]:
    queue = verifier.client.get("/api/staff/verification").json()
    [found] = queue["identifier_conflicts"]
    detail: dict[str, Any] = verifier.client.get(
        f"/api/staff/vehicle-conflicts/{found['id']}"
    ).json()["conflict"]
    return detail


def test_only_the_verification_decides(
    owner: Client, two: dict[str, str], support: Member, verifier: Member
) -> None:
    create(owner, two["a"], two["la"], vin=VIN)
    create(owner, two["b"], two["lb"], vin=VIN)
    conflict = IdentifierConflict.objects.get()
    assert support.client.get("/api/staff/vehicle-conflicts").status_code == 403
    denied = sp(support, f"vehicle-conflicts/{conflict.id}/decide", {"outcome": "error"})
    assert denied.status_code == 403
    detail = _conflict(verifier)
    # The verification sees the parties to decide.
    assert {p["company"]["name"] for p in detail["parties"]} == {"Alpha Rent", "Beta Service"}
    assert SystemJournalEntry.objects.filter(action="verification.conflict_viewed").exists()


def test_different_vehicles_get_an_exception(
    owner: Client, two: dict[str, str], verifier: Member
) -> None:
    create(owner, two["a"], two["la"], plate="1กก 777", plate_country="TH")
    second = create(owner, two["b"], two["lb"], plate="1กก777", plate_country="TH").json()
    conflict = IdentifierConflict.objects.get()

    response = sp(
        verifier,
        f"vehicle-conflicts/{conflict.id}/decide",
        {"outcome": "exception", "reason": "Re-issued plate, different VINs", "evidence": "Docs"},
    )

    assert response.status_code == 200, response.content
    conflict.refresh_from_db()
    assert (conflict.is_open, conflict.review) == (False, "exception")
    # The same match between them is not opened again.
    vehicle = card(owner, two["b"], second["vehicle"]["id"])
    assert details(owner, two["b"], vehicle, color="Red").status_code == 200
    assert IdentifierConflict.objects.count() == 1
    # A third vehicle with the plate opens a new conflict.
    create(owner, two["a"], two["la"], plate="1กก 777", plate_country="TH", name="Third")
    assert IdentifierConflict.objects.filter(is_open=True).count() == 1
    # Both companies were told the decision by the number of the conflict.
    told = Notification.objects.filter(event="vehicle.identifier_conflict_decided")
    assert told.count() == 2
    assert all(n.params["number"] == conflict.number for n in told)
    assert all("Alpha" not in str(n.params) for n in told)
    entry = SystemJournalEntry.objects.get(action="verification.identifier_conflict_decided")
    assert entry.reason == "Re-issued plate, different VINs"


def test_an_exception_is_revised_by_a_new_decision(
    owner: Client, two: dict[str, str], verifier: Member
) -> None:
    create(owner, two["a"], two["la"], vin=VIN)
    create(owner, two["b"], two["lb"], vin=VIN)
    conflict = IdentifierConflict.objects.get()
    sp(verifier, f"vehicle-conflicts/{conflict.id}/decide", {"outcome": "exception", "reason": "x"})

    revised = sp(
        verifier,
        f"vehicle-conflicts/{conflict.id}/decide",
        {"outcome": "insufficient", "reason": "New evidence"},
    )

    assert revised.status_code == 200, revised.content
    conflict.refresh_from_db()
    assert (conflict.is_open, conflict.review) == (True, "insufficient")
    decisions = list(IdentifierConflictDecision.objects.order_by("decided_at"))
    assert [d.outcome for d in decisions] == ["exception", "insufficient"]
    assert decisions[1].revises_id == decisions[0].id


def test_wrong_data_keep_the_conflict_until_corrected(
    owner: Client, two: dict[str, str], verifier: Member
) -> None:
    create(owner, two["a"], two["la"], vin=VIN)
    second = create(owner, two["b"], two["lb"], vin=VIN).json()["vehicle"]
    conflict = IdentifierConflict.objects.get()

    sp(verifier, f"vehicle-conflicts/{conflict.id}/decide", {"outcome": "error", "reason": "Typo"})

    conflict.refresh_from_db()
    assert (conflict.is_open, conflict.review) == (True, "awaiting_correction")
    # Both records keep it; each vehicle keeps its own ID.
    assert Vehicle.objects.filter(merged_into__isnull=False).count() == 0
    assert details(owner, two["b"], second, vin="JTDBR32E720000001").status_code == 200
    conflict.refresh_from_db()
    assert conflict.review == "cause_removed"


def test_one_physical_vehicle_links_the_cards(
    owner: Client, two: dict[str, str], verifier: Member
) -> None:
    # A rental fleet and a service company that serves the vehicle for a client (§ 8.3.2).
    first = create(owner, two["a"], two["la"], vin=VIN, year=2019).json()["vehicle"]
    second = create(
        owner, two["b"], two["lb"], vin=VIN, relation="client", color="White", year=2020
    ).json()["vehicle"]
    conflict = IdentifierConflict.objects.get()

    response = sp(
        verifier,
        f"vehicle-conflicts/{conflict.id}/decide",
        {
            "outcome": "same_vehicle",
            "reason": "Same VIN and papers",
            "primary": first["vehicle_id"],
        },
    )

    assert response.status_code == 200, response.content
    main = Vehicle.objects.get(public_id=first["vehicle_id"])
    other = Vehicle.objects.get(public_id=second["vehicle_id"])
    assert other.merged_into_id == main.id
    assert VehicleCard.objects.get(pk=second["id"]).vehicle_id == main.id
    # Empty values of the main entity are filled; a different one is the card's working value.
    assert main.color == "White"
    shown = card(owner, two["b"], second["id"])
    assert shown["vehicle_id"] == first["vehicle_id"]
    assert shown["working_values"]["year"]["value"] == 2020
    assert shown["working_values"]["year"]["global"]["value"] == 2019
    # The company A keeps its own card and data.
    assert card(owner, two["a"], first["id"])["working_values"] == {}
    conflict.refresh_from_db()
    assert (conflict.is_open, conflict.review) == (False, "same_vehicle")
    # A link is not undone by a new decision.
    again = sp(
        verifier, f"vehicle-conflicts/{conflict.id}/decide", {"outcome": "error", "reason": "x"}
    )
    assert err(again) == "CONFLICT_CLOSED"


def test_the_entity_with_owners_is_the_main_one(
    owner: Client, two: dict[str, str], verifier: Member
) -> None:
    first = create(owner, two["a"], two["la"], vin=VIN).json()["vehicle"]
    second = create(owner, two["b"], two["lb"], vin=VIN, relation="client").json()["vehicle"]
    VehicleOwnership.objects.create(
        vehicle=Vehicle.objects.get(public_id=second["vehicle_id"]),
        owner_company_id=VehicleCard.objects.get(pk=second["id"]).company_id,
        started_at=timezone.now(),
    )
    conflict = IdentifierConflict.objects.get()
    body = {"outcome": "same_vehicle", "reason": "Same", "primary": first["vehicle_id"]}
    assert err(sp(verifier, f"vehicle-conflicts/{conflict.id}/decide", body)) == (
        "VEHICLE_LINK_SECONDARY_HAS_OWNERS"
    )
    body["primary"] = second["vehicle_id"]
    assert sp(verifier, f"vehicle-conflicts/{conflict.id}/decide", body).status_code == 200


# --- Merge and split inside a company (§ 8.6.5–8.6.6) --------------------------------------------


@pytest.fixture
def pair(owner: Client, two: dict[str, str], verifier: Member) -> dict[str, Any]:
    """Two cards of one company found to be one vehicle: the second waits for the merge."""
    cmp, location = two["a"], two["la"]
    first = create(owner, cmp, location, vin=VIN, name="Yaris 1", color="White").json()["vehicle"]
    second = create(owner, cmp, location, vin=VIN, name="Yaris 2", color="Red").json()["vehicle"]
    conflict = IdentifierConflict.objects.get()
    shown = card(owner, cmp, second["id"])["identifier_conflicts"][0]
    # The company sees its own other card in the conflict.
    assert shown["own_cards"] == [{"id": first["id"], "name": "Yaris 1"}]
    decided = sp(
        verifier,
        f"vehicle-conflicts/{conflict.id}/decide",
        {"outcome": "same_vehicle", "reason": "Duplicate", "primary": first["vehicle_id"]},
    )
    assert decided.status_code == 200, decided.content
    return {"cmp": cmp, "first": first, "second": second}


def _merge(owner: Client, pair: dict[str, Any], **body: Any) -> Any:
    cmp, first, second = pair["cmp"], pair["first"], pair["second"]
    return post(
        owner,
        f"/api/c/{cmp}/vehicles/{first['id']}/merge",
        {"duplicate_id": second["id"], "code": CODE, **body},
        key(),
    )


def test_merge_needs_the_comparison_choices_and_a_code(owner: Client, pair: dict[str, Any]) -> None:
    cmp, first, second = pair["cmp"], pair["first"], pair["second"]
    waiting = card(owner, cmp, second["id"])
    assert waiting["pending_local_merge"]["cards"] == [{"id": first["id"], "name": "Yaris 1"}]
    assert card(owner, cmp, first["id"])["merge_candidates"][0]["id"] == second["id"]
    preview = owner.get(
        f"/api/c/{cmp}/vehicles/{first['id']}/merge/preview?duplicate={second['id']}"
    ).json()["comparison"]
    differing = {f["key"] for f in preview["fields"] if not f["same"]}
    assert differing == {"name", "characteristic.color"}
    assert preview["obstacles"] == []

    # Without a code sent first: refused.
    assert (
        _merge(
            owner, pair, choices={"name": "primary", "characteristic.color": "duplicate"}
        ).json()["error"]["code"]
        == "CODE_EXPIRED"
    )
    sent = post(
        owner,
        f"/api/c/{cmp}/vehicles/{first['id']}/merge/code",
        {"duplicate_id": second["id"]},
        key(),
    )
    assert sent.status_code == 200, sent.content
    missing = _merge(owner, pair, choices={"name": "primary"})
    assert missing.json()["error"]["code"] == "VEHICLE_MERGE_CHOICE_REQUIRED"
    wrong = _merge(owner, pair, code="000000", choices={"name": "primary"})
    assert wrong.json()["error"]["code"] == "CODE_INVALID"

    done = _merge(owner, pair, choices={"name": "primary", "characteristic.color": "duplicate"})

    assert done.status_code == 201, done.content
    duplicate = VehicleCard.objects.get(pk=second["id"])
    assert (duplicate.state, str(duplicate.merged_into_id)) == ("merged", first["id"])
    names = [v["name"] for v in owner.get(f"/api/c/{cmp}/vehicles").json()["vehicles"]]
    assert names == ["Yaris 1"]
    main = card(owner, cmp, first["id"])
    assert main["merged_cards"][0]["id"] == second["id"]
    assert main["working_values"]["color"]["value"] == "Red"
    assert card(owner, cmp, second["id"])["merged_into"]["id"] == first["id"]
    # Closed for new operations; the ID is kept.
    with pytest.raises(ApiError) as refused:
        services.require_open_for_new(duplicate)
    assert refused.value.code == "VEHICLE_CARD_MERGED"
    assert services.rental_readiness(duplicate)["missing"][0] == "card_merged"
    assert AuditEvent.objects.filter(action="vehicle.merged").count() == 2


def test_different_entities_are_not_merged(owner: Client, two: dict[str, str]) -> None:
    cmp, location = two["a"], two["la"]
    first = create(owner, cmp, location, vin=VIN).json()["vehicle"]
    second = create(owner, cmp, location, vin=VIN, name="Other").json()["vehicle"]
    preview = owner.get(
        f"/api/c/{cmp}/vehicles/{first['id']}/merge/preview?duplicate={second['id']}"
    ).json()["comparison"]
    assert "different_entities" in preview["obstacles"]


def test_merge_and_split_need_their_rights(owner: Client, pair: dict[str, Any]) -> None:
    cmp, first, second = pair["cmp"], pair["first"], pair["second"]
    location = VehicleCard.objects.get(pk=first["id"]).accounting_location_id
    manager, _ = hire(
        owner,
        cmp,
        "m@example.com",
        [{"type": "set", "set": "rental_manager", "locations": [str(location)]}],
    )
    preview = manager.get(
        f"/api/c/{cmp}/vehicles/{first['id']}/merge/preview?duplicate={second['id']}"
    )
    assert preview.status_code == 403
    post(
        owner,
        f"/api/c/{cmp}/vehicles/{first['id']}/merge/code",
        {"duplicate_id": second["id"]},
        key(),
    )
    _merge(owner, pair, choices={"name": "primary", "characteristic.color": "primary"})
    merge_id = VehicleCardMerge.objects.get().id
    denied = post(
        manager,
        f"/api/c/{cmp}/vehicles/{first['id']}/merges/{merge_id}/split",
        {"reason": "x"},
        key(),
    )
    assert denied.status_code == 403


def test_split_without_new_records_restores_the_cards(owner: Client, pair: dict[str, Any]) -> None:
    cmp, first, second = pair["cmp"], pair["first"], pair["second"]
    post(
        owner,
        f"/api/c/{cmp}/vehicles/{first['id']}/merge/code",
        {"duplicate_id": second["id"]},
        key(),
    )
    _merge(owner, pair, choices={"name": "duplicate", "characteristic.color": "primary"})
    assert VehicleCard.objects.get(pk=first["id"]).name == "Yaris 2"
    merge_id = VehicleCardMerge.objects.get().id

    split = post(
        owner,
        f"/api/c/{cmp}/vehicles/{first['id']}/merges/{merge_id}/split",
        {"reason": "Merged by mistake"},
        key(),
    )

    assert split.status_code == 200, split.content
    duplicate = VehicleCard.objects.get(pk=second["id"])
    assert (duplicate.state, duplicate.merged_into_id) == ("active", None)
    # The main card gets back its own name.
    assert VehicleCard.objects.get(pk=first["id"]).name == "Yaris 1"
    assert VehicleCardMerge.objects.get().state == "split"


def test_new_records_are_assigned_by_hand(owner: Client, pair: dict[str, Any]) -> None:
    cmp, first, second = pair["cmp"], pair["first"], pair["second"]
    post(
        owner,
        f"/api/c/{cmp}/vehicles/{first['id']}/merge/code",
        {"duplicate_id": second["id"]},
        key(),
    )
    _merge(owner, pair, choices={"name": "primary", "characteristic.color": "primary"})
    merge = VehicleCardMerge.objects.get()
    main = VehicleCard.objects.get(pk=first["id"])
    ctx = CommandContext(
        actor=Actor(user_id=main.created_by_user_id), command="t", idempotency_key="t"
    )
    reading = services.record_reading(
        ctx,
        main,
        measured_at=timezone.now() - timedelta(minutes=1),
        value=Decimal("1000"),
        source_type="import",
    )
    path = f"/api/c/{cmp}/vehicles/{first['id']}/merges/{merge.id}/split"
    preview = owner.get(f"{path}/preview").json()
    assert [r["key"] for r in preview["records"]] == [f"reading:{reading.id}"]

    unassigned = post(owner, path, {"reason": "Mistake"}, key())
    assert unassigned.json()["error"]["code"] == "VEHICLE_SPLIT_UNASSIGNED"
    VehicleCard.objects.get(pk=second["id"])  # still merged
    assert VehicleCardMerge.objects.get().state == "merged"

    done = post(
        owner,
        path,
        {"reason": "Mistake", "assignments": {f"reading:{reading.id}": "duplicate"}},
        key(),
    )

    assert done.status_code == 200, done.content
    assert str(OdometerReading.objects.get(pk=reading.id).vehicle_card_id) == second["id"]
    assert VehicleCardMerge.objects.get().assignments == [
        {"record": f"reading:{reading.id}", "card": "duplicate"}
    ]


# --- Confirmed characteristics (§ 8.2.1) ---------------------------------------------------------


def test_confirmed_values_are_protected_and_corrected_by_proposal(
    owner: Client, two: dict[str, str], verifier: Member, support: Member
) -> None:
    cmp = two["a"]
    vehicle = create(owner, cmp, two["la"], vin=VIN, year=2019).json()["vehicle"]
    vhl = vehicle["vehicle_id"]
    denied = sp(support, f"vehicles/{vhl}/confirm", {"items": [{"field": "year"}], "reason": "x"})
    assert denied.status_code == 403
    confirmed = sp(
        verifier,
        f"vehicles/{vhl}/confirm",
        {"items": [{"field": "year"}, {"field": "vin"}], "reason": "Registration papers"},
    )
    assert confirmed.status_code == 200, confirmed.content
    shown = card(owner, cmp, vehicle["id"])
    assert set(shown["confirmed_fields"]) == {"year", "vin"}

    # Not changed by a direct edit.
    edited = details(owner, cmp, shown, year=2020)
    assert edited.json()["error"]["code"] == "VEHICLE_CHARACTERISTIC_CONFIRMED"
    # Other values still are.
    assert details(owner, cmp, shown, color="Blue").status_code == 200

    proposed = post(
        owner,
        f"/api/c/{cmp}/vehicles/{vehicle['id']}/proposals",
        {
            "field": "year",
            "year": 2020,
            "reason": "Year in the papers is 2020",
            "use_working": True,
        },
        key(),
    )
    assert proposed.status_code == 201, proposed.content
    working = proposed.json()["vehicle"]["working_values"]["year"]
    assert (working["value"], working["global"]["value"], working["origin"]) == (
        2020,
        2019,
        "proposal",
    )

    [listed] = verifier.client.get("/api/staff/vehicle-proposals").json()["proposals"]
    accepted = sp(
        verifier, f"vehicle-proposals/{listed['id']}/decide", {"accept": True, "reason": "Checked"}
    )
    assert accepted.status_code == 200, accepted.content

    after = card(owner, cmp, vehicle["id"])
    assert after["characteristics"]["year"] == 2020
    assert after["working_values"] == {}
    events = list(VehicleCharacteristicEvent.objects.filter(field="year").order_by("decided_at"))
    assert [(e.action, e.before, e.after) for e in events] == [
        ("confirmed", 2019, 2019),
        ("corrected", 2019, 2020),
    ]
    assert events[1].initiator_company_id is not None
    assert Notification.objects.filter(event="vehicle.characteristic_decided").exists()


def test_a_proposal_only_for_a_confirmed_value(owner: Client, two: dict[str, str]) -> None:
    vehicle = create(owner, two["a"], two["la"], year=2019).json()["vehicle"]
    response = post(
        owner,
        f"/api/c/{two['a']}/vehicles/{vehicle['id']}/proposals",
        {"field": "year", "year": 2020, "reason": "x"},
        key(),
    )
    assert response.json()["error"]["code"] == "VEHICLE_CHARACTERISTIC_NOT_CONFIRMED"


# --- Catalog and ownership periods (§ 8.1.11, § 8.3.16) ------------------------------------------


def test_the_support_extends_the_catalog_and_links_other_values(
    owner: Client, two: dict[str, str], support: Member, verifier: Member
) -> None:
    vehicle = create(
        owner, two["a"], two["la"], vehicle_type="passenger_car", make_other="Qwertz Motors"
    ).json()["vehicle"]
    assert verifier.client.get("/api/staff/vehicle-catalog/other").status_code == 403
    groups = support.client.get("/api/staff/vehicle-catalog/other").json()["other"]
    assert groups[0]["make_other"] == "Qwertz Motors" and groups[0]["vehicles"] == [
        vehicle["vehicle_id"]
    ]

    made = sp(
        support,
        "vehicle-catalog/makes",
        {"name": "Qwertz Motors", "vehicle_type": "passenger_car", "reason": "Checked the brand"},
    )
    assert made.status_code == 201, made.content
    code = made.json()["make"]["code"]
    linked = sp(
        support,
        "vehicle-catalog/link",
        {"vehicles": [vehicle["vehicle_id"]], "make": code, "reason": "Same brand"},
    )
    assert linked.status_code == 200, linked.content
    stored = Vehicle.objects.get(public_id=vehicle["vehicle_id"])
    assert (stored.make_id, stored.make_other) == (code, "")
    assert VehicleMake.objects.filter(code=code).exists()


def test_a_confirmed_period_is_corrected_as_a_new_version(
    owner: Client, two: dict[str, str], verifier: Member
) -> None:
    vehicle = create(owner, two["a"], two["la"]).json()["vehicle"]
    stored = Vehicle.objects.get(public_id=vehicle["vehicle_id"])
    now = timezone.now()
    old = VehicleOwnership.objects.create(
        vehicle=stored,
        owner_company_id=VehicleCard.objects.get(pk=vehicle["id"]).company_id,
        started_at=now - timedelta(days=400),
        ended_at=now - timedelta(days=100),
    )
    VehicleOwnership.objects.create(
        vehicle=stored,
        owner_company_id=VehicleCard.objects.get(pk=vehicle["id"]).company_id,
        started_at=now - timedelta(days=50),
    )
    path = f"vehicles/{vehicle['vehicle_id']}/ownerships/{old.id}/correct"
    body: dict[str, Any] = {
        "started_at": (now - timedelta(days=500)).isoformat(),
        "ended_at": (now - timedelta(days=10)).isoformat(),
        "initiator": "Owner by email",
        "reason": "Contract",
    }
    assert err(sp(verifier, path, body)) == "OWNERSHIP_PERIODS_OVERLAP"

    body["ended_at"] = (now - timedelta(days=60)).isoformat()
    body["started_approx"] = True
    done = sp(verifier, path, body)

    assert done.status_code == 200, done.content
    old.refresh_from_db()
    assert old.started_approx and old.ended_at is not None
    revision = VehicleOwnershipRevision.objects.get()
    assert revision.before["ended_at"] != revision.after["ended_at"]
    assert SystemJournalEntry.objects.filter(action="verification.ownership_corrected").exists()


def test_a_card_waiting_for_the_merge_is_not_edited(owner: Client, pair: dict[str, Any]) -> None:
    waiting = card(owner, pair["cmp"], pair["second"]["id"])
    response = details(owner, pair["cmp"], waiting, color="Green")
    assert response.json()["error"]["code"] == "VEHICLE_PENDING_LOCAL_MERGE"


def test_the_cancel_of_an_import_leaves_the_conflicts(owner: Client, two: dict[str, str]) -> None:
    create(owner, two["a"], two["la"], vin=VIN)
    second = create(owner, two["b"], two["lb"], vin=VIN).json()["vehicle"]
    company_id = VehicleCard.objects.get(pk=second["id"]).company_id

    services.remove_imported(company_id, [VehicleCard.objects.get(pk=second["id"]).id])

    # The other card keeps the history of the closed conflict.
    conflict = IdentifierConflict.objects.get()
    assert (conflict.is_open, conflict.review) == (False, "cause_removed")
    assert not VehicleCard.objects.filter(pk=second["id"]).exists()
