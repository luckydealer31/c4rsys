"""Lifecycle of a vehicle card: archive and restore, the end of the management and the relation,
write-off and disposal, history of owners and plates, the rest of the characteristics (ТЗ § 8.1.6,
§ 8.1.12, § 8.2.6, § 8.2.11, § 8.3.17, § 8.4.2, § 8.7.5–8.7.6)."""

from datetime import timedelta
from typing import Any

import pytest
from django.test import Client
from django.utils import timezone

from c4rsys.fleet.models import Vehicle, VehicleCard, VehicleCardPeriod, VehicleOwnership
from c4rsys.platform.models import AuditEvent
from tests.accounts.helpers import post
from tests.companies.helpers import create_company, hire, key
from tests.rentals.scenario import Scenario, build
from tests.warehouse.test_operations import location as warehouse_location
from tests.warehouse.test_operations import warehouse_of

pytestmark = pytest.mark.django_db

GRANT = {"type": "grant", "locations": "all", "contexts": "all", "cash_accounts": "all"}


@pytest.fixture
def s(owner: Client) -> Scenario:
    return build(owner)


def _card(client: Client, s: Scenario, card: str) -> dict[str, Any]:
    body: dict[str, Any] = client.get(f"/api/c/{s.cmp}/vehicles/{card}").json()["vehicle"]
    return body


def _managed(owner: Client, s: Scenario, name: str = "Managed Civic") -> dict[str, Any]:
    created = post(
        owner,
        f"/api/c/{s.cmp}/vehicles",
        {
            "name": name,
            "relation": "managed",
            "location_id": s.location,
            "odometer_unit": "km",
            "owner_kind": "person",
            "owner_name": "Somchai",
        },
        key(),
    )
    assert created.status_code == 201, created.content
    card: dict[str, Any] = created.json()["vehicle"]
    return card


def _do(client: Client, s: Scenario, card: dict[str, Any], action: str, **body: Any) -> Any:
    data = {"expected_version": card["version"], **body}
    return post(client, f"/api/c/{s.cmp}/vehicles/{card['id']}/{action}", data, key())


def test_own_and_managed_cards_are_not_archived_directly(owner: Client, s: Scenario) -> None:
    own = _card(owner, s, s.vehicle)
    assert _do(owner, s, own, "archive", reason="x").json()["error"]["code"] == (
        "VEHICLE_OWNERSHIP_NOT_ENDED"
    )
    managed = _managed(owner, s)
    assert _do(owner, s, managed, "archive", reason="x").json()["error"]["code"] == (
        "VEHICLE_MANAGEMENT_ACTIVE"
    )
    assert _do(owner, s, managed, "archive").json()["error"]["field"] == "reason"


def test_end_of_management_with_a_booking_is_refused_then_archives(
    owner: Client, s: Scenario
) -> None:
    managed = _managed(owner, s)
    now = timezone.now()
    booked = post(
        owner,
        f"/api/c/{s.cmp}/bookings",
        {
            "vehicle_card_id": managed["id"],
            "planned_start": (now + timedelta(days=1)).isoformat(),
            "planned_end": (now + timedelta(days=2)).isoformat(),
        },
        key(),
    )
    assert booked.status_code == 201, booked.content
    refused = _do(owner, s, managed, "end-management", reason="Owner took it back")
    assert refused.json()["error"]["code"] == "VEHICLE_HAS_OPERATIONS"
    assert refused.json()["error"]["params"]["blockers"][0]["kind"] == "booking"
    rental = booked.json()["rental"]
    post(
        owner,
        f"/api/c/{s.cmp}/bookings/{rental['id']}/cancel",
        {"expected_version": rental["version"], "reason": "owner"},
        key(),
    )
    ended = _do(owner, s, managed, "end-management", reason="Owner took it back")
    assert ended.status_code == 200, ended.content
    body = ended.json()["vehicle"]
    assert body["state"] == "archived"
    assert body["archive"]["reason"] == "Owner took it back"
    assert [p["end_basis"] for p in body["periods"]] == ["management_ended"]
    entry = AuditEvent.objects.get(action="vehicle.management_ended")
    assert entry.reason == "Owner took it back"


def test_end_of_management_keeps_a_client_card_and_takes_it_back(
    owner: Client, s: Scenario
) -> None:
    managed = _managed(owner, s)
    ended = _do(owner, s, managed, "end-management", reason="Ended", then="client")
    assert ended.status_code == 200, ended.content
    body = ended.json()["vehicle"]
    assert (body["relation"], body["location_id"], body["state"]) == ("client", None, "active")
    assert [(p["relation"], p["ended_at"] is None) for p in body["periods"]] == [
        ("client", True),
        ("managed", False),
    ]
    # § 8.3.17: back into the management straight to the garage.
    back = _do(
        owner,
        s,
        body,
        "take-into-management",
        reason="New contract",
        location_id=s.location,
        owner_kind="person",
        owner_name="Somchai",
    )
    assert back.status_code == 200, back.content
    body = back.json()["vehicle"]
    assert (body["relation"], body["location_id"]) == ("managed", s.location)
    assert body["contexts"] == ["rental"]
    assert len(body["periods"]) == 3


def test_client_card_archive_and_restore_with_a_new_period(owner: Client, s: Scenario) -> None:
    created = post(
        owner,
        f"/api/c/{s.cmp}/vehicles",
        {"name": "Client Jazz", "relation": "client", "odometer_unit": "km"},
        key(),
    ).json()["vehicle"]
    archived = _do(owner, s, created, "archive", reason="Service ended")
    assert archived.status_code == 200, archived.content
    body = archived.json()["vehicle"]
    assert body["state"] == "archived" and body["archive"]["reason"] == "Service ended"
    # The archived card is read only.
    again = _do(owner, s, body, "archive", reason="again")
    assert again.json()["error"]["code"] == "VEHICLE_ARCHIVED"
    renamed = owner.patch(
        f"/api/c/{s.cmp}/vehicles/{body['id']}",
        data={"name": "New", "expected_version": body["version"]},
        content_type="application/json",
        headers=key(),
    )
    assert renamed.json()["error"]["code"] == "VEHICLE_ARCHIVED"
    # § 8.7.6: the company becomes the manager: the earlier card comes back.
    restored = _do(
        owner,
        s,
        body,
        "restore",
        relation="managed",
        location_id=s.location,
        reason="Owner gave it to management",
        owner_kind="none",
    )
    assert restored.status_code == 200, restored.content
    body = restored.json()["vehicle"]
    assert (body["state"], body["relation"], body["location_id"]) == (
        "active",
        "managed",
        s.location,
    )
    assert [p["start_basis"] for p in body["periods"]] == ["restored", "created"]
    assert VehicleCard.objects.filter(vehicle__public_id=body["vehicle_id"]).count() == 1


def test_restore_refused_while_another_company_runs_it(owner: Client, s: Scenario) -> None:
    created = post(
        owner,
        f"/api/c/{s.cmp}/vehicles",
        {"name": "Client Jazz", "relation": "client", "odometer_unit": "km"},
        key(),
    ).json()["vehicle"]
    body = _do(owner, s, created, "archive", reason="done").json()["vehicle"]
    other = create_company(owner, key="c-2", display_name="Other").json()["company"]
    vehicle = Vehicle.objects.get(public_id=body["vehicle_id"])
    from c4rsys.companies.models import Company, Location

    theirs = Company.objects.get(public_id=other["public_id"])
    other_location = Location.objects.create(company=theirs, name="X")
    VehicleCard.objects.create(
        company=theirs,
        vehicle=vehicle,
        name="Theirs",
        relation="own",
        accounting_location=other_location,
        odometer_unit="km",
        created_by_user_id=vehicle.id,
    )
    refused = _do(
        owner,
        s,
        body,
        "restore",
        relation="managed",
        location_id=s.location,
        reason="x",
        owner_kind="none",
    )
    assert refused.json()["error"]["code"] == "VEHICLE_OPERATIONAL_ELSEWHERE"


def test_rights_of_archive_and_relation(owner: Client, s: Scenario) -> None:
    editor, _ = hire(
        owner,
        s.cmp,
        "editor@example.com",
        [{**GRANT, "permission": p} for p in ("vehicle.view", "vehicle.edit")],
    )
    managed = _managed(owner, s)
    assert _do(editor, s, managed, "end-management", reason="x").status_code == 403
    assert _do(editor, s, managed, "archive", reason="x").status_code == 403


def _to_warehouse(owner: Client, cmp: str) -> tuple[str, dict[str, Any], str]:
    place = warehouse_location(owner, cmp, "Store")
    ctx = warehouse_of(place)
    car = post(
        owner,
        f"/api/c/{cmp}/vehicles",
        {"name": "Old Vios", "location_id": place["id"], "odometer_unit": "km"},
        key(),
    ).json()["vehicle"]
    off = owner.patch(
        f"/api/c/{cmp}/vehicles/{car['id']}",
        data={"contexts": [], "expected_version": car["version"]},
        content_type="application/json",
        headers=key(),
    ).json()["vehicle"]
    moved = post(
        owner,
        f"/api/c/{cmp}/vehicles/{car['id']}/to-warehouse",
        {"context_id": ctx, "expected_version": off["version"]},
        key(),
    )
    assert moved.status_code == 200, moved.content
    return car["id"], moved.json()["vehicle"], ctx


def test_write_off_only_from_the_warehouse_with_its_right(owner: Client, s: Scenario) -> None:
    garage = _card(owner, s, s.vehicle)
    refused = _do(owner, s, garage, "dispose", kind="write_off", reason="Total loss")
    assert refused.json()["error"]["code"] == "VEHICLE_NOT_IN_WAREHOUSE"
    car, stored, _ctx = _to_warehouse(owner, s.cmp)
    seller, _ = hire(
        owner,
        s.cmp,
        "store@example.com",
        [{**GRANT, "permission": p} for p in ("vehicle.view", "warehouse.view")],
    )
    assert _do(seller, s, stored, "dispose", kind="disposal", reason="x").status_code == 403
    vehicle = Vehicle.objects.get(public_id=stored["vehicle_id"])
    VehicleOwnership.objects.create(
        vehicle=vehicle,
        owner_company_id=VehicleCard.objects.get(pk=car).company_id,
        started_at=timezone.now() - timedelta(days=30),
    )
    done = _do(owner, s, stored, "dispose", kind="disposal", reason="Scrapped")
    assert done.status_code == 200, done.content
    body = done.json()["vehicle"]
    assert body["state"] == "archived"
    assert body["disposal"]["kind"] == "disposal"
    # The company's confirmed ownership ends.
    assert VehicleOwnership.objects.get(vehicle=vehicle).ended_at is not None
    restored = _do(owner, s, body, "restore", relation="own", location_id=s.location, reason="x")
    assert restored.json()["error"]["code"] == "VEHICLE_DISPOSED"


def test_history_of_owners_and_plates_and_more_characteristics(owner: Client, s: Scenario) -> None:
    card = _card(owner, s, s.vehicle)
    body = {
        "name": card["name"],
        "expected_version": card["version"],
        "plate": "1กข 1234",
        "plate_country": "TH",
        "manufacturer": "Toyota Motor Thailand",
        "manufacture_country": "th",
        "modification": "1.2 G",
        "tank_volume_l": "42",
        "battery_kwh": "",
        "range_km": 600,
        "gears": 6,
        "curb_weight_kg": 1050,
        "gross_weight_kg": 1500,
        "payload_kg": 450,
        "cargo_volume_m3": "0.35",
        "inventory_number": "INV-7",
        "barcode": "QR-123",
    }
    first = owner.put(
        f"/api/c/{s.cmp}/vehicles/{s.vehicle}/details",
        data=body,
        content_type="application/json",
        headers=key(),
    )
    assert first.status_code == 200, first.content
    facts = first.json()["vehicle"]["characteristics"]
    assert (facts["manufacture_country"], facts["tank_volume_l"], facts["cargo_volume_m3"]) == (
        "TH",
        "42.0",
        "0.35",
    )
    assert (facts["inventory_number"], facts["barcode"], facts["gears"]) == ("INV-7", "QR-123", 6)
    # § 8.2.11: a new plate is a new period; the earlier one stays with its end.
    second = owner.put(
        f"/api/c/{s.cmp}/vehicles/{s.vehicle}/details",
        data={**body, "plate": "2กข 5678", "expected_version": first.json()["vehicle"]["version"]},
        content_type="application/json",
        headers=key(),
    )
    plates = second.json()["vehicle"]["plates_history"]
    assert [(p["number"], p["valid_to"] is None) for p in plates] == [
        ("2กข 5678", True),
        ("1กข 1234", False),
    ]
    # § 8.2.6: the owners with the start and the end of their periods.
    vehicle = Vehicle.objects.get(cards__pk=s.vehicle)
    company_id = VehicleCard.objects.get(pk=s.vehicle).company_id
    VehicleOwnership.objects.create(
        vehicle=vehicle,
        owner_user_id=vehicle.id,
        started_at=timezone.now() - timedelta(days=400),
        ended_at=timezone.now() - timedelta(days=100),
    )
    VehicleOwnership.objects.create(
        vehicle=vehicle, owner_company_id=company_id, started_at=timezone.now() - timedelta(days=99)
    )
    owners = _card(owner, s, s.vehicle)["owners_history"]
    assert [(o["owner"], o["name"], o["ended_at"] is None) for o in owners] == [
        ("this_company", "Test Rent", True),
        ("person", "", False),
    ]
    assert VehicleCardPeriod.objects.filter(vehicle_card__pk=s.vehicle).count() == 1
