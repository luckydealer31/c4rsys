"""Warehouse, part С8: return of a vehicle from the warehouse to the garage and the dashboard
«Warehouse» (ТЗ § 8.4.1, § 21.1.1, § 21.6.1)."""

from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest import mock

import pytest
from django.test import Client
from django.utils import timezone

from c4rsys.companies.models import Company
from c4rsys.fleet.models import VehicleCard
from c4rsys.warehouse import dashboard
from c4rsys.warehouse.models import Lot
from tests.accounts.helpers import post
from tests.companies.helpers import create_company, hire, key
from tests.warehouse.test_operations import item as new_item
from tests.warehouse.test_operations import (
    location,
    packaging,
    receive_stock,
    vehicle,
    warehouse_of,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def cmp(owner: Client) -> str:
    public_id: str = create_company(owner, timezone="Asia/Bangkok", main_currency="THB").json()[
        "company"
    ]["public_id"]
    return public_id


def put(client: Client, url: str, body: dict[str, Any]) -> Any:
    return client.put(url, data=body, content_type="application/json", headers=key())


def to_warehouse(owner: Client, cmp: str, car: str, ctx: str) -> None:
    off = owner.patch(
        f"/api/c/{cmp}/vehicles/{car}",
        data={"contexts": [], "expected_version": 1},
        content_type="application/json",
        headers=key(),
    ).json()["vehicle"]
    moved = post(
        owner,
        f"/api/c/{cmp}/vehicles/{car}/to-warehouse",
        {"context_id": ctx, "expected_version": off["version"]},
        key(),
    )
    assert moved.status_code == 200, moved.content


def test_vehicle_returns_to_the_garage(owner: Client, cmp: str) -> None:
    phuket = location(owner, cmp, "Phuket")
    ctx = warehouse_of(phuket)
    car = vehicle(owner, cmp, phuket["id"])
    url = f"/api/c/{cmp}/vehicles/{car}/to-garage"
    assert (
        post(owner, url, {"expected_version": 1}, key()).json()["error"]["code"]
        == "VEHICLE_NOT_IN_WAREHOUSE"
    )
    to_warehouse(owner, cmp, car, ctx)

    # A vehicle reserved in an open order stays in the warehouse.
    put(owner, f"/api/c/{cmp}/warehouse/vehicles/{car}/price", {"amount": "300000"})
    client = post(
        owner,
        f"/api/c/{cmp}/clients",
        {
            "kind": "person",
            "first_name": "A",
            "last_name": "B",
            "contacts": [{"kind": "phone", "value": "+66811111111"}],
        },
        key(),
    ).json()["client"]["id"]
    order = post(owner, f"/api/c/{cmp}/warehouse/sales/start", {"client_id": client}, key()).json()[
        "order"
    ]["id"]
    lines_url = f"/api/c/{cmp}/warehouse/orders/{order}/sale-lines"
    line = post(owner, lines_url, {"context_id": ctx, "vehicle_id": car}, key()).json()["lines"][0]
    version = VehicleCard.objects.get(pk=car).version
    blocked = post(owner, url, {"expected_version": version}, key())
    assert blocked.json()["error"]["code"] == "VEHICLE_HAS_OPERATIONS"

    owner.delete(f"{lines_url}/{line['id']}", headers=key())
    back = post(owner, url, {"expected_version": version}, key())
    assert back.status_code == 200, back.content
    card = VehicleCard.objects.get(pk=car)
    assert card.storage_context_id is None and card.accounting_location_id is not None


def test_dashboard(owner: Client, cmp: str) -> None:
    phuket = location(owner, cmp, "Phuket")
    ctx = warehouse_of(phuket)
    krabi = warehouse_of(location(owner, cmp, "Krabi"))
    oil = new_item(owner, cmp, "Oil", "liter")
    can = packaging(owner, cmp, oil["id"], "4")
    pads = new_item(owner, cmp, "Pads", "piece")
    receive_stock(
        owner,
        cmp,
        ctx,
        [
            {"item_id": oil["id"], "packaging_id": can, "packs": 3, "unit_cost": "100"},
            {"item_id": pads["id"], "quantity": 2},
        ],
    )
    Lot.objects.update(expiry_date=timezone.localdate() + timedelta(days=5))
    put(
        owner,
        f"/api/c/{cmp}/warehouse/items/{oil['id']}/price",
        {"packaging_id": can, "amount": "500"},
    )
    put(
        owner,
        f"/api/c/{cmp}/warehouse/items/{oil['id']}/minimum",
        {"context_id": ctx, "quantity": "20"},
    )
    client = post(
        owner,
        f"/api/c/{cmp}/clients",
        {
            "kind": "person",
            "first_name": "A",
            "last_name": "B",
            "contacts": [{"kind": "phone", "value": "+66811111111"}],
        },
        key(),
    ).json()["client"]["id"]
    order = post(owner, f"/api/c/{cmp}/warehouse/sales/start", {"client_id": client}, key()).json()[
        "order"
    ]["id"]
    lot = str(Lot.objects.get().id)
    post(
        owner,
        f"/api/c/{cmp}/warehouse/orders/{order}/sale-lines",
        {
            "context_id": ctx,
            "item_id": oil["id"],
            "lot_id": lot,
            "packaging_id": can,
            "quantity": "4",
        },
        key(),
    )

    board = owner.get(f"/api/c/{cmp}/warehouse/dashboard").json()["dashboard"]
    rows = {row["item"]["name"]: row for row in board["stock"]}
    assert (rows["Oil"]["actual"], rows["Oil"]["reserved"], rows["Oil"]["available"]) == (
        "12.00",
        "4.00",
        "8.00",
    )
    # Pads came without a cost and without a price: both sums are incomplete.
    assert board["value"]["cost"] == {"amount": "1200.00", "incomplete": True}
    assert board["value"]["sale"]["incomplete"] is True
    assert [row["item"]["name"] for row in board["below_minimum"]] == ["Oil"]
    assert board["expiring"][0]["expired"] is False
    assert board["period"]["receipts"]["count"] == 1
    assert board["scope"]["partial"] is False

    by_category = owner.get(f"/api/c/{cmp}/warehouse/dashboard?item_id={pads['id']}").json()[
        "dashboard"
    ]
    assert [row["item"]["name"] for row in by_category["stock"]] == ["Pads"]
    wrong = owner.get(f"/api/c/{cmp}/warehouse/dashboard?from=2026-10-10&to=2026-10-01")
    assert wrong.json()["error"]["code"] == "PERIOD_INVALID"

    grant = {
        "type": "grant",
        "permission": "warehouse.view",
        "locations": [phuket["id"]],
        "contexts": "all",
        "cash_accounts": "all",
    }
    viewer, _ = hire(owner, cmp, "viewer@example.com", [grant])
    partial = viewer.get(f"/api/c/{cmp}/warehouse/dashboard").json()["dashboard"]
    assert partial["scope"] == {"warehouses": ["Phuket"], "partial": True}
    assert krabi


def test_today_is_in_the_company_time_zone() -> None:
    """At 17:30 UTC it is already the next day in Bangkok (§ 2.7.5)."""
    evening = datetime(2026, 10, 25, 17, 30, tzinfo=UTC)
    with mock.patch("django.utils.timezone.now", return_value=evening):
        assert dashboard.company_today(Company(timezone="Asia/Bangkok")) == date(2026, 10, 26)
        assert dashboard.company_today(Company(timezone="")) == date(2026, 10, 25)
