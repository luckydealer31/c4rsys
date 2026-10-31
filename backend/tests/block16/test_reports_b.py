"""Block 16 Б: reports — the missing blocks of «Company overview», the context filter and the
category and tariff filters of «Rental and fleet» (ТЗ § 21.2.2, § 21.2.5, § 21.3.1, § 21.4.1)."""

from typing import Any

import pytest
from django.test import Client

from c4rsys.companies.models import ActivityContext, Company
from c4rsys.fleet.models import Vehicle, VehicleCard
from c4rsys.warehouse.models import InventoryCount
from tests.accounts.helpers import post
from tests.companies.helpers import create_company, hire, key, named_user
from tests.orders.test_close import frozen_clock  # noqa: F401
from tests.rentals.scenario import Scenario, build, issue

pytestmark = pytest.mark.django_db


@pytest.fixture
def s(owner: Client) -> Scenario:
    return build(owner)


def _blocks(client: Client, s: Scenario, code: str = "overview", query: str = "") -> Any:
    response = client.get(f"/api/c/{s.cmp}/reports/{code}?{query}")
    assert response.status_code == 200, response.content
    return {b["code"]: b for b in response.json()["blocks"]}


def _vin(owner: Client, s: Scenario, vin: str) -> None:
    version = owner.get(f"/api/c/{s.cmp}/vehicles/{s.vehicle}").json()["vehicle"]["version"]
    response = owner.put(
        f"/api/c/{s.cmp}/vehicles/{s.vehicle}/details",
        data={
            "name": "Toyota Yaris",
            "vehicle_type": "passenger_car",
            "vin": vin,
            "expected_version": version,
        },
        content_type="application/json",
        headers=key(),
    )
    assert response.status_code == 200, response.content


def test_unavailable_vehicles_block_and_list(owner: Client, s: Scenario) -> None:
    blocks = _blocks(owner, s)
    assert blocks["fleet.unavailable"]["value"] == 0
    assert blocks["fleet.available"]["value"] == 1
    restricted = post(
        owner,
        f"/api/c/{s.cmp}/vehicles/{s.vehicle}/restrictions",
        {"reason": "technical_fault", "forbid_rental_issue": True},
        key(),
    )
    assert restricted.status_code == 201, restricted.content
    blocks = _blocks(owner, s)
    assert blocks["fleet.unavailable"]["value"] == 1
    # PROPOSAL: a restricted vehicle is not counted as available.
    assert blocks["fleet.available"]["value"] == 0
    assert blocks["fleet.unavailable"]["link"] == "/vehicles?only=unavailable"
    listed = owner.get(f"/api/c/{s.cmp}/vehicles?only=unavailable").json()["vehicles"]
    assert [v["id"] for v in listed] == [s.vehicle]
    assert owner.get(f"/api/c/{s.cmp}/vehicles?only=identifier_conflict").json()["vehicles"] == []
    assert owner.get(f"/api/c/{s.cmp}/vehicles?only=wrong").status_code == 400


def test_identifier_conflicts_block(owner: Client, s: Scenario) -> None:
    other = Client()
    named_user(other, "other@example.com")
    cmp = create_company(other, display_name="Other", timezone="Asia/Bangkok").json()["company"][
        "public_id"
    ]
    location = post(other, f"/api/c/{cmp}/locations", {"name": "Krabi"}, key()).json()["location"][
        "id"
    ]
    created = post(
        other,
        f"/api/c/{cmp}/vehicles",
        {"name": "Car", "location_id": location, "odometer_unit": "km", "vin": "JTDBR32E720123456"},
        key(),
    )
    assert created.status_code == 201, created.content
    _vin(owner, s, "jtdbr32e720123456")
    blocks = _blocks(owner, s)
    assert blocks["fleet.identifier_conflicts"]["value"] == 1
    # A conflict alone does not make the vehicle unavailable (§ 8.6.3).
    assert blocks["fleet.unavailable"]["value"] == 0
    listed = owner.get(f"/api/c/{s.cmp}/vehicles?only=identifier_conflict").json()["vehicles"]
    assert [v["id"] for v in listed] == [s.vehicle]


def test_unconfirmed_counts_and_requests(owner: Client, s: Scenario) -> None:
    company = Company.objects.get(public_id=s.cmp)
    post(owner, f"/api/c/{s.cmp}/locations/{s.location}/contexts", {"type": "warehouse"}, key())
    warehouse = ActivityContext.objects.get(location_id=s.location, type="warehouse")
    user_id = VehicleCard.objects.get(pk=s.vehicle).created_by_user_id
    InventoryCount.objects.create(
        company=company, number=1, context=warehouse, state="submitted", created_by_user_id=user_id
    )
    InventoryCount.objects.create(
        company=company, number=2, context=warehouse, state="draft", created_by_user_id=user_id
    )
    blocks = _blocks(owner, s)
    assert blocks["warehouse.unconfirmed_counts"]["value"] == 1
    assert blocks["warehouse.unconfirmed_counts"]["link"] == "/warehouse?tab=inventory"
    assert blocks["company.pending_requests"]["value"] == 0
    # Another company asks to link its counterparty card with this company (§ 6.3.8).
    asker = Client()
    named_user(asker, "asker@example.com")
    cmp = create_company(asker, display_name="Asker").json()["company"]["public_id"]
    card = post(
        asker,
        f"/api/c/{cmp}/counterparties",
        {
            "kind": "company",
            "company_name": "Test Rent",
            "contacts": [{"kind": "phone", "value": "+66 81 234 5678"}],
        },
        key(),
    ).json()["counterparty"]["id"]
    asked = post(
        asker,
        f"/api/c/{cmp}/profile-links",
        {"card_type": "counterparty", "card_id": card, "target": s.cmp},
        key(),
    )
    assert asked.status_code == 201, asked.content
    assert _blocks(owner, s)["company.pending_requests"]["value"] == 1
    # Only who answers for the whole company sees the requests.
    reader, _ = hire(
        owner,
        s.cmp,
        "r@example.com",
        [
            {"type": "grant", "permission": "reports.view"},
            {"type": "grant", "permission": "vehicle.view"},
        ],
    )
    assert "company.pending_requests" not in _blocks(reader, s)


@pytest.mark.usefixtures("frozen_clock")
def test_context_filter_keeps_blocks_of_its_type(owner: Client, s: Scenario) -> None:
    post(owner, f"/api/c/{s.cmp}/locations/{s.location}/contexts", {"type": "service"}, key())
    rental = ActivityContext.objects.get(location_id=s.location, type="rental")
    service = ActivityContext.objects.get(location_id=s.location, type="service")
    issue(owner, s)
    by_rental = _blocks(owner, s, query=f"context={rental.id}")
    assert by_rental["rental.active"]["value"] == 1
    assert by_rental["rental.active"]["area"] == {"whole": False, "locations": ["Phuket"]}
    assert "service.maintenance_overdue" not in by_rental
    by_service = _blocks(owner, s, query=f"context={service.id}")
    assert "rental.active" not in by_service and "service.maintenance_overdue" in by_service
    # The fleet is not a context block: the location filter only.
    assert by_service["fleet.total"]["value"] == 1
    other = post(owner, f"/api/c/{s.cmp}/locations", {"name": "Kata"}, key()).json()["location"]
    wrong = owner.get(f"/api/c/{s.cmp}/reports/overview?context={rental.id}&location={other['id']}")
    assert wrong.status_code == 400
    unknown = owner.get(
        f"/api/c/{s.cmp}/reports/overview?context=0190a3e0-0000-7000-8000-000000000000"
    )
    assert unknown.status_code == 400


@pytest.mark.usefixtures("frozen_clock")
def test_rental_category_and_tariff_filters(owner: Client, s: Scenario) -> None:
    issue(owner, s)
    all_cars = _blocks(owner, s, "rental")
    assert all_cars["rental.active"]["value"] == 1
    assert len(all_cars["fleet.by_vehicle"]["rows"]) == 1
    Vehicle.objects.filter(cards__id=s.vehicle).update(vehicle_type="passenger_car")
    cars = _blocks(owner, s, "rental", "category=passenger_car")
    assert cars["rental.active"]["value"] == 1
    trucks = _blocks(owner, s, "rental", "category=truck")
    assert trucks["rental.active"]["value"] == 0 and trucks["fleet.by_vehicle"]["rows"] == []
    assert _blocks(owner, s, "rental", f"tariff={s.tariff}")["rental.active"]["value"] == 1
    payload = owner.get(f"/api/c/{s.cmp}/reports/rental?category=truck").json()["filters"]
    assert payload["category"] == "truck"
