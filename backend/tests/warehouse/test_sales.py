"""Warehouse, part С6: sale of goods and vehicles through an order — reserve and price snapshot,
closing, removal, reopening of the order and cancellation of a wrongly closed sale (ТЗ § 8.3.9,
§ 11.3.2, § 11.9, § 12.2.3; decisions of 25.10.2026)."""

from decimal import Decimal
from typing import Any

import pytest
from django.test import Client

from c4rsys.finance.charges import charged_for_order
from c4rsys.fleet.models import VehicleCard
from c4rsys.warehouse.models import Asset, LayerUse, LotStock, SaleLine
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


@pytest.fixture
def setup(owner: Client, cmp: str) -> dict[str, Any]:
    phuket = location(owner, cmp, "Phuket")
    ctx = warehouse_of(phuket)
    oil = new_item(owner, cmp, "Oil", "liter")
    can = packaging(owner, cmp, oil["id"], "4")
    pads = new_item(owner, cmp, "Pads", "piece")
    receive_stock(
        owner,
        cmp,
        ctx,
        [
            {"item_id": oil["id"], "packaging_id": can, "packs": 3, "unit_cost": "100"},
            {"item_id": oil["id"], "quantity": "5", "unit_cost": "90"},
            {"item_id": pads["id"], "quantity": 2, "unit_cost": "500"},
        ],
    )
    base = f"/api/c/{cmp}/warehouse/items"
    put(owner, f"{base}/{oil['id']}/price", {"packaging_id": can, "amount": "1500"})
    put(owner, f"{base}/{oil['id']}/price", {"amount": "400"})
    put(owner, f"{base}/{pads['id']}/price", {"amount": "900"})
    client = post(
        owner,
        f"/api/c/{cmp}/clients",
        {
            "kind": "person",
            "first_name": "Somchai",
            "last_name": "K",
            "contacts": [{"kind": "phone", "value": "+66811111111"}],
        },
        key(),
    ).json()["client"]["id"]
    order = post(owner, f"/api/c/{cmp}/warehouse/sales/start", {"client_id": client}, key())
    assert order.status_code == 201, order.content
    return {
        "ctx": ctx,
        "location": phuket["id"],
        "oil": oil["id"],
        "can": can,
        "pads": pads["id"],
        "order": order.json()["order"]["id"],
    }


def lines_url(cmp: str, order: str) -> str:
    return f"/api/c/{cmp}/warehouse/orders/{order}/sale-lines"


def lot_of(oil: str, packaging_id: str | None) -> str:
    rows = LotStock.objects.filter(lot__item_id=oil, quantity__gt=0)
    form = (
        rows.filter(packaging__isnull=True)
        if packaging_id is None
        else rows.filter(packaging_id=packaging_id)
    )
    row = form.first()
    assert row is not None
    return str(row.lot_id)


def test_reserve_snapshot_and_sale_on_closing(
    owner: Client, cmp: str, setup: dict[str, Any]
) -> None:
    url = lines_url(cmp, setup["order"])
    packs = post(
        owner,
        url,
        {
            "context_id": setup["ctx"],
            "item_id": setup["oil"],
            "lot_id": lot_of(setup["oil"], setup["can"]),
            "packaging_id": setup["can"],
            "quantity": "8",
        },
        key(),
    )
    assert packs.status_code == 201, packs.content
    assert packs.json()["lines"][0]["amount"] == "3000.00"
    pad = Asset.objects.first()
    assert pad is not None
    added = post(
        owner,
        url,
        {"context_id": setup["ctx"], "item_id": setup["pads"], "asset_ids": [str(pad.id)]},
        key(),
    )
    assert added.status_code == 201, added.content

    # § 11.9.1: the reserve is unavailable to other operations.
    car = vehicle(owner, cmp, setup["location"])
    taken = post(
        owner,
        f"/api/c/{cmp}/warehouse/operations/consume",
        {
            "context_id": setup["ctx"],
            "vehicle_id": car,
            "lines": [{"item_id": setup["pads"], "asset_ids": [str(pad.id)]}],
        },
        key(),
    )
    assert taken.json()["error"]["code"] == "ASSET_NOT_AVAILABLE"
    too_much = post(
        owner,
        url,
        {
            "context_id": setup["ctx"],
            "item_id": setup["oil"],
            "lot_id": lot_of(setup["oil"], setup["can"]),
            "packaging_id": setup["can"],
            "quantity": "8",
        },
        key(),
    )
    assert too_much.json()["error"]["code"] == "STOCK_NOT_ENOUGH"

    # § 11.3.2: a later price change does not touch the snapshot.
    put(owner, f"/api/c/{cmp}/warehouse/items/{setup['pads']}/price", {"amount": "1000"})
    first = SaleLine.objects.first()
    assert first is not None
    assert charged_for_order(first.company_id, first.order_id) == {"THB": Decimal("3900")}

    order = owner.get(f"/api/c/{cmp}/orders/{setup['order']}").json()["order"]
    closed = post(
        owner,
        f"/api/c/{cmp}/orders/{setup['order']}/close",
        {"expected_version": order["version"]},
        key(),
    )
    assert closed.status_code == 200, closed.content
    assert LotStock.objects.get(
        lot_id=lot_of(setup["oil"], setup["can"]), packaging_id=setup["can"]
    ).quantity == Decimal("4.00")
    pad.refresh_from_db()
    assert pad.state == "sold"
    assert set(SaleLine.objects.values_list("state", flat=True)) == {"sold"}
    assert {use.unit_cost for use in LayerUse.objects.all()} == {Decimal("100")}


def test_price_required_and_removal_releases(
    owner: Client, cmp: str, setup: dict[str, Any]
) -> None:
    url = lines_url(cmp, setup["order"])
    put(owner, f"/api/c/{cmp}/warehouse/items/{setup['oil']}/price", {"amount": None})
    no_price = post(
        owner,
        url,
        {
            "context_id": setup["ctx"],
            "item_id": setup["oil"],
            "lot_id": lot_of(setup["oil"], None),
            "quantity": "1.5",
        },
        key(),
    )
    assert no_price.json()["error"]["code"] == "PRICE_NOT_SET"
    put(owner, f"/api/c/{cmp}/warehouse/items/{setup['oil']}/price", {"amount": "400"})
    loose = post(
        owner,
        url,
        {
            "context_id": setup["ctx"],
            "item_id": setup["oil"],
            "lot_id": lot_of(setup["oil"], None),
            "quantity": "1.5",
        },
        key(),
    ).json()["lines"][0]
    assert loose["amount"] == "600.00"
    removed = owner.delete(f"{url}/{loose['id']}", headers=key())
    assert removed.json()["lines"][0]["state"] == "removed"
    assert charged_for_order(
        SaleLine.objects.get().company_id, SaleLine.objects.get().order_id
    ) == {"THB": Decimal("0")}


def test_reopen_and_cancel_a_wrong_sale(owner: Client, cmp: str, setup: dict[str, Any]) -> None:
    url = lines_url(cmp, setup["order"])
    pad = Asset.objects.first()
    assert pad is not None
    line = post(
        owner,
        url,
        {"context_id": setup["ctx"], "item_id": setup["pads"], "asset_ids": [str(pad.id)]},
        key(),
    ).json()["lines"][0]
    order = owner.get(f"/api/c/{cmp}/orders/{setup['order']}").json()["order"]
    post(
        owner,
        f"/api/c/{cmp}/orders/{setup['order']}/close",
        {"expected_version": order["version"]},
        key(),
    )
    order = owner.get(f"/api/c/{cmp}/orders/{setup['order']}").json()["order"]
    assert order["state"] == "closed"

    closed_cancel = post(owner, f"{url}/{line['id']}/cancel", {"reason": "Wrong client"}, key())
    assert closed_cancel.json()["error"]["code"] == "ORDER_CLOSED"
    no_reason = post(
        owner,
        f"/api/c/{cmp}/orders/{setup['order']}/reopen",
        {"expected_version": order["version"]},
        key(),
    )
    assert no_reason.json()["error"]["field"] == "reason"
    reopened = post(
        owner,
        f"/api/c/{cmp}/orders/{setup['order']}/reopen",
        {"expected_version": order["version"], "reason": "Wrong client"},
        key(),
    )
    assert reopened.status_code == 200, reopened.content
    cancelled = post(owner, f"{url}/{line['id']}/cancel", {"reason": "Wrong client"}, key())
    assert cancelled.json()["lines"][0]["state"] == "cancelled"
    pad.refresh_from_db()
    assert pad.state == "in_stock"

    grant = {"type": "grant", "locations": "all", "contexts": "all", "cash_accounts": "all"}
    seller, _ = hire(
        owner,
        cmp,
        "seller@example.com",
        [{**grant, "permission": "warehouse.sell"}, {**grant, "permission": "order.view"}],
    )
    again = post(
        seller,
        url,
        {"context_id": setup["ctx"], "item_id": setup["pads"], "asset_ids": [str(pad.id)]},
        key(),
    ).json()["lines"]
    sold = next(entry for entry in again if entry["state"] == "reserved")
    assert post(seller, f"{url}/{sold['id']}/cancel", {"reason": "x"}, key()).status_code == 403


def test_vehicle_sale_ends_ownership(owner: Client, cmp: str, setup: dict[str, Any]) -> None:
    car = vehicle(owner, cmp, setup["location"])
    off = owner.patch(
        f"/api/c/{cmp}/vehicles/{car}",
        data={"contexts": [], "expected_version": 1},
        content_type="application/json",
        headers=key(),
    ).json()["vehicle"]
    moved = post(
        owner,
        f"/api/c/{cmp}/vehicles/{car}/to-warehouse",
        {"context_id": setup["ctx"], "expected_version": off["version"]},
        key(),
    )
    assert moved.status_code == 200, moved.content
    url = lines_url(cmp, setup["order"])
    no_price = post(owner, url, {"context_id": setup["ctx"], "vehicle_id": car}, key())
    assert no_price.json()["error"]["code"] == "PRICE_NOT_SET"
    put(owner, f"/api/c/{cmp}/warehouse/vehicles/{car}/price", {"amount": "350000"})
    added = post(owner, url, {"context_id": setup["ctx"], "vehicle_id": car}, key())
    assert added.json()["lines"][0]["amount"] == "350000.00"
    again = post(owner, url, {"context_id": setup["ctx"], "vehicle_id": car}, key())
    assert again.json()["error"]["code"] == "VEHICLE_RESERVED"
    order = owner.get(f"/api/c/{cmp}/orders/{setup['order']}").json()["order"]
    # The owner may close with debt; the sale ends the seller's ownership (§ 8.3.9).
    closing = post(
        owner,
        f"/api/c/{cmp}/orders/{setup['order']}/close",
        {"expected_version": order["version"]},
        key(),
    )
    assert closing.status_code == 200, closing.content
    assert VehicleCard.objects.get(pk=car).state == "archived"
