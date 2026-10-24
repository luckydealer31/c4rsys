"""Vehicles: global VHL record, company card, odometer, readiness (ТЗ § 8.1, § 8.2.12, § 8.5.1,
§ 8.7.7)."""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from django.db import IntegrityError, transaction
from django.test import Client

from c4rsys.companies.models import Company, Location
from c4rsys.fleet import services
from c4rsys.fleet.models import OdometerReading, Vehicle, VehicleCard
from c4rsys.platform.commands import Actor, CommandContext, CommandResult, run_command
from c4rsys.platform.errors import ApiError
from c4rsys.platform.models import AuditEvent
from tests.accounts.helpers import post
from tests.companies.helpers import create_company, hire, key

pytestmark = pytest.mark.django_db

BKK = ZoneInfo("Asia/Bangkok")


@pytest.fixture
def cmp(owner: Client) -> str:
    public_id: str = create_company(owner, timezone="Asia/Bangkok", main_currency="THB").json()[
        "company"
    ]["public_id"]
    return public_id


def _location(client: Client, cmp: str, name: str, *contexts: str) -> str:
    location_id: str = post(
        client, f"/api/c/{cmp}/locations", {"name": name}, headers=key()
    ).json()["location"]["id"]
    for kind in contexts:
        post(client, f"/api/c/{cmp}/locations/{location_id}/contexts", {"type": kind}, key())
    return location_id


def _create(client: Client, cmp: str, location_id: str = "", **data: Any) -> Any:
    body = {"name": "Toyota Yaris", "location_id": location_id, "odometer_unit": "km", **data}
    return post(client, f"/api/c/{cmp}/vehicles", body, headers=key())


def test_create_own_vehicle_with_vhl_id(owner: Client, cmp: str) -> None:
    # § 8.1.3, § 8.1.10, § 8.1.14: only the name is required; a global VHL ID is issued.
    phuket = _location(owner, cmp, "Phuket", "rental")

    response = _create(owner, cmp, phuket)

    assert response.status_code == 201, response.content
    vehicle = response.json()["vehicle"]
    assert vehicle["vehicle_id"].startswith("VHL-") and len(vehicle["vehicle_id"]) == 16
    assert (vehicle["relation"], vehicle["location_id"], vehicle["odometer_unit"]) == (
        "own",
        phuket,
        "km",
    )
    assert vehicle["odometer"] is None
    assert Vehicle.objects.get().public_id == vehicle["vehicle_id"]
    assert AuditEvent.objects.filter(action="vehicle.created").count() == 1


def test_usage_contexts_are_the_applicable_ones_existing_at_creation(
    owner: Client, cmp: str
) -> None:
    # § 8.5.1: existing operational contexts of the company are enabled; service is implied and
    # the warehouse is not a usage context (decision of 24.10.2026).
    krabi = _location(owner, cmp, "Krabi", "service", "warehouse")
    first = _create(owner, cmp, krabi).json()["vehicle"]
    phuket = _location(owner, cmp, "Phuket", "rental")
    second = _create(owner, cmp, phuket, name="Honda Click").json()["vehicle"]

    assert first["contexts"] == []
    assert second["contexts"] == ["rental"]
    assert owner.get(f"/api/c/{cmp}/vehicles/{first['id']}").json()["vehicle"]["contexts"] == []


def test_rental_readiness_of_8_7_7(owner: Client, cmp: str) -> None:
    ready_location = _location(owner, cmp, "Phuket", "rental")
    other = _location(owner, cmp, "Krabi")

    ready = _create(owner, cmp, ready_location).json()["vehicle"]
    unready = _create(owner, cmp, other, name="Second").json()["vehicle"]

    assert ready["rental_readiness"] == {"ready": True, "missing": []}
    assert unready["rental_readiness"] == {
        "ready": False,
        "missing": ["location_without_rental_context"],
    }


def _patch(client: Client, cmp: str, vehicle_id: str, data: dict[str, Any]) -> Any:
    return client.patch(
        f"/api/c/{cmp}/vehicles/{vehicle_id}",
        data=data,
        content_type="application/json",
        headers=key(),
    )


def test_vehicle_created_before_rental_context_is_allowed_by_hand(owner: Client, cmp: str) -> None:
    # § 8.5.1: contexts added later are not switched on automatically; the company switches them
    # on in the card (decision 22.10.2026).
    location_id = _location(owner, cmp, "Phuket")
    vehicle = _create(owner, cmp, location_id).json()["vehicle"]
    post(owner, f"/api/c/{cmp}/locations/{location_id}/contexts", {"type": "rental"}, key())
    before = owner.get(f"/api/c/{cmp}/vehicles/{vehicle['id']}").json()["vehicle"]
    assert before["rental_readiness"] == {"ready": False, "missing": ["rental_not_allowed"]}

    after = _patch(owner, cmp, vehicle["id"], {"contexts": ["rental"], "expected_version": 1})

    assert after.json()["vehicle"]["contexts"] == ["rental"]
    assert after.json()["vehicle"]["rental_readiness"] == {"ready": True, "missing": []}


def test_switching_rental_off_removes_readiness(owner: Client, cmp: str) -> None:
    location_id = _location(owner, cmp, "Phuket", "rental", "service")
    vehicle = _create(owner, cmp, location_id).json()["vehicle"]

    changed = _patch(owner, cmp, vehicle["id"], {"contexts": [], "expected_version": 1})

    assert changed.json()["vehicle"]["contexts"] == []
    assert changed.json()["vehicle"]["rental_readiness"]["missing"] == ["rental_not_allowed"]
    event = AuditEvent.objects.get(action="vehicle.changed")
    assert (event.before, event.after) == ({"contexts": ["rental"]}, {"contexts": []})


@pytest.mark.parametrize("contexts", [["taxi"], ["service"], ["warehouse"], "rental", [1]])
def test_only_usage_context_types(owner: Client, cmp: str, contexts: Any) -> None:
    vehicle = _create(owner, cmp, _location(owner, cmp, "Phuket", "rental")).json()["vehicle"]

    response = _patch(owner, cmp, vehicle["id"], {"contexts": contexts, "expected_version": 1})

    assert response.status_code == 400


@pytest.mark.parametrize(
    ("data", "field"),
    [
        ({"name": ""}, "name"),
        ({"odometer_unit": "ft"}, "odometer_unit"),
        ({"relation": "rented"}, "relation"),
        ({"location_id": "01a1628f-ca1c-71ab-a48b-80eede8d99b1"}, "location_id"),
    ],
)
def test_invalid_input(owner: Client, cmp: str, data: dict[str, Any], field: str) -> None:
    body = {"location_id": _location(owner, cmp, "Phuket", "rental"), **data}

    response = _create(owner, cmp, **body)

    assert response.status_code == 400
    assert response.json()["error"]["field"] == field
    assert not VehicleCard.objects.exists()


def test_name_changes_but_location_and_unit_do_not(owner: Client, cmp: str) -> None:
    phuket = _location(owner, cmp, "Phuket", "rental")
    vehicle = _create(owner, cmp, phuket).json()["vehicle"]

    renamed = _patch(owner, cmp, vehicle["id"], {"name": "Yaris 1", "expected_version": 1})
    refused = _patch(owner, cmp, vehicle["id"], {"odometer_unit": "mi", "expected_version": 2})

    assert renamed.json()["vehicle"]["name"] == "Yaris 1"
    assert renamed.json()["vehicle"]["vehicle_id"] == vehicle["vehicle_id"]
    assert refused.json()["error"]["code"] == "VEHICLE_FIELD_NOT_EDITABLE"


def test_vehicle_rights_follow_accounting_location(owner: Client, cmp: str) -> None:
    # § 23.5: view, create and edit are separate; scope is the vehicle's location.
    phuket = _location(owner, cmp, "Phuket", "rental")
    krabi = _location(owner, cmp, "Krabi", "rental")
    in_phuket = _create(owner, cmp, phuket).json()["vehicle"]
    in_krabi = _create(owner, cmp, krabi, name="Krabi car").json()["vehicle"]
    manager, _ = hire(
        owner,
        cmp,
        "m@example.com",
        [{"type": "set", "set": "rental_manager", "locations": [phuket]}],
    )

    names = [v["name"] for v in manager.get(f"/api/c/{cmp}/vehicles").json()["vehicles"]]
    assert names == ["Toyota Yaris"]
    assert manager.get(f"/api/c/{cmp}/vehicles/{in_krabi['id']}").status_code == 404
    assert _create(manager, cmp, phuket).status_code == 403
    rename = manager.patch(
        f"/api/c/{cmp}/vehicles/{in_phuket['id']}",
        data={"name": "X", "expected_version": 1},
        content_type="application/json",
        headers=key(),
    )
    assert rename.status_code == 403


def test_card_location_must_belong_to_same_company(owner: Client, cmp: str) -> None:
    # A05: the database forbids cross-company links.
    _create(owner, cmp, _location(owner, cmp, "Phuket", "rental"))
    other = Company.objects.get(
        public_id=create_company(owner, key="c-2", display_name="Other").json()["company"][
            "public_id"
        ]
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        VehicleCard.objects.create(
            company=other,
            vehicle=Vehicle.objects.create(public_id="VHL-TEST00000000"),
            name="Foreign",
            relation="own",
            accounting_location=Location.objects.get(),
            odometer_unit="km",
            created_by_user_id=other.created_by_id,
        )


# --- Odometer sequence (§ 8.2.12) --------------------------------------------------------------


def _record(card: VehicleCard, measured_at: datetime, value: str) -> Any:
    actor = Actor(user_id=card.created_by_user_id, company_id=card.company_id)

    def handler(ctx: CommandContext) -> CommandResult:
        reading = services.record_reading(
            ctx, card, measured_at=measured_at, value=Decimal(value), source_type="test"
        )
        return CommandResult(201, {"id": str(reading.id)})

    return run_command(
        command="test.odometer",
        actor=actor,
        idempotency_key=f"{measured_at.isoformat()}-{value}",
        payload={},
        handler=handler,
    )


@pytest.fixture
def card(owner: Client, cmp: str) -> VehicleCard:
    vehicle = _create(owner, cmp, _location(owner, cmp, "Phuket", "rental")).json()["vehicle"]
    return VehicleCard.objects.get(pk=UUID(vehicle["id"]))


def test_current_reading_is_the_latest_measurement(card: VehicleCard) -> None:
    base = datetime(2026, 11, 1, 10, tzinfo=BKK)
    _record(card, base, "1000")
    _record(card, base + timedelta(days=2), "1300")
    _record(card, base + timedelta(days=1), "1100")  # an older inspection entered later

    current = services.current_reading(card.id)

    assert current is not None and current.value == Decimal("1300")


@pytest.mark.parametrize(
    ("offset_days", "value"),
    [(1, "999"), (1, "1301"), (3, "1299"), (-1, "1001"), (0, "1001")],
)
def test_reading_conflicting_with_neighbours_is_refused(
    card: VehicleCard, offset_days: int, value: str
) -> None:
    base = datetime(2026, 11, 1, 10, tzinfo=BKK)
    _record(card, base, "1000")
    _record(card, base + timedelta(days=2), "1300")

    with pytest.raises(ApiError) as error:
        _record(card, base + timedelta(days=offset_days), value)

    assert error.value.code == "ODOMETER_SEQUENCE_CONFLICT"
    assert error.value.params["conflicts"]
    assert OdometerReading.objects.count() == 2


def test_reading_between_neighbours_and_equal_values_are_accepted(card: VehicleCard) -> None:
    base = datetime(2026, 11, 1, 10, tzinfo=BKK)
    _record(card, base, "1000")
    _record(card, base + timedelta(days=2), "1300")

    _record(card, base + timedelta(days=1), "1000")
    _record(card, base + timedelta(days=3), "1300")

    assert OdometerReading.objects.count() == 4
    assert {r.unit for r in OdometerReading.objects.all()} == {"km"}


# --- Switching a context off and the transfer to a warehouse (decision of 24.10.2026) --------


def _warehouse(owner: Client, cmp: str, location_id: str) -> str:
    response = post(
        owner, f"/api/c/{cmp}/locations/{location_id}/contexts", {"type": "warehouse"}, key()
    )
    assert response.status_code in (200, 201), response.content
    location = owner.get(f"/api/c/{cmp}/locations/{location_id}").json()["location"]
    context_id: str = next(c["id"] for c in location["contexts"] if c["type"] == "warehouse")
    return context_id


def _to_warehouse(owner: Client, cmp: str, vehicle_id: str, context_id: str, version: int) -> Any:
    return post(
        owner,
        f"/api/c/{cmp}/vehicles/{vehicle_id}/to-warehouse",
        {"context_id": context_id, "expected_version": version},
        key(),
    )


def test_own_vehicle_goes_to_warehouse_only_without_contexts(owner: Client, cmp: str) -> None:
    location_id = _location(owner, cmp, "Phuket", "rental")
    storage = _warehouse(owner, cmp, location_id)
    vehicle = _create(owner, cmp, location_id).json()["vehicle"]

    refused = _to_warehouse(owner, cmp, vehicle["id"], storage, vehicle["version"])
    assert refused.json()["error"]["code"] == "VEHICLE_HAS_CONTEXTS"

    off = _patch(owner, cmp, vehicle["id"], {"contexts": [], "expected_version": 1}).json()
    moved = _to_warehouse(owner, cmp, vehicle["id"], storage, off["vehicle"]["version"])

    assert moved.status_code == 200, moved.content
    body = moved.json()["vehicle"]
    assert body["storage"]["context_id"] == storage
    assert body["vehicle_id"] == vehicle["vehicle_id"]  # the same asset, history kept
    assert "in_warehouse" in body["rental_readiness"]["missing"]
    back_on = _patch(owner, cmp, vehicle["id"], {"contexts": ["rental"], "expected_version": 3})
    assert back_on.json()["error"]["code"] == "VEHICLE_IN_WAREHOUSE"
    assert AuditEvent.objects.filter(action="vehicle.transferred_to_warehouse").count() == 1


def test_managed_vehicle_is_not_transferred(owner: Client, cmp: str) -> None:
    location_id = _location(owner, cmp, "Phuket")
    storage = _warehouse(owner, cmp, location_id)
    vehicle = _create(owner, cmp, location_id, relation="managed").json()["vehicle"]

    response = _to_warehouse(owner, cmp, vehicle["id"], storage, vehicle["version"])

    assert response.json()["error"]["code"] == "VEHICLE_NOT_OWN"


def test_transfer_needs_its_own_right(owner: Client, cmp: str) -> None:
    location_id = _location(owner, cmp, "Phuket")
    storage = _warehouse(owner, cmp, location_id)
    vehicle = _create(owner, cmp, location_id).json()["vehicle"]
    rights = [
        {
            "type": "grant",
            "permission": p,
            "locations": "all",
            "contexts": "all",
            "cash_accounts": "all",
        }
        for p in ("vehicle.view", "vehicle.edit")
    ]
    staff, _ = hire(owner, cmp, "staff@example.com", rights)

    response = _to_warehouse(staff, cmp, vehicle["id"], storage, vehicle["version"])

    assert response.status_code == 403
