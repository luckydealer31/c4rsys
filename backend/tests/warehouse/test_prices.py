"""Warehouse, part С5: selling prices by item or lot, mode change, own price of a unit and of a
vehicle, minimum stock with a warning (ТЗ § 11.7.2, § 11.7.4–11.7.6, § 11.7.8)."""

from decimal import Decimal
from typing import Any

import pytest
from django.test import Client

from c4rsys.warehouse import prices
from c4rsys.warehouse.models import Asset, Item, Lot, Packaging
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
            {"item_id": pads["id"], "quantity": 2, "unit_cost": "500"},
        ],
    )
    return {"ctx": ctx, "oil": oil["id"], "can": can, "pads": pads["id"], "location": phuket["id"]}


def put(client: Client, url: str, body: dict[str, Any]) -> Any:
    return client.put(url, data=body, content_type="application/json", headers=key())


def url(cmp: str, item_id: str, tail: str) -> str:
    return f"/api/c/{cmp}/warehouse/items/{item_id}/{tail}"


def test_item_prices_of_packagings_and_loose_are_independent(
    owner: Client, cmp: str, setup: dict[str, Any]
) -> None:
    put(owner, url(cmp, setup["oil"], "price"), {"packaging_id": setup["can"], "amount": "1200"})
    done = put(owner, url(cmp, setup["oil"], "price"), {"amount": "350"}).json()["prices"]
    assert {(p["packaging_id"], p["amount"]) for p in done["item_prices"]} == {
        (setup["can"], "1200.00"),
        (None, "350.00"),
    }
    oil = Item.objects.get(pk=setup["oil"])
    can = Packaging.objects.get(pk=setup["can"])
    assert prices.price_for(oil, packaging=can) == Decimal("1200")
    assert prices.price_for(oil) == Decimal("350")
    precise = put(owner, url(cmp, setup["oil"], "price"), {"amount": "1.001"})
    assert precise.json()["error"]["code"] == "AMOUNT_TOO_PRECISE"
    lot_in_item_mode = put(
        owner, url(cmp, setup["oil"], "price"), {"lot_id": str(Lot.objects.get().id), "amount": "1"}
    )
    assert lot_in_item_mode.json()["error"]["code"] == "PRICE_ITEM_MODE"
    cleared = put(owner, url(cmp, setup["oil"], "price"), {"amount": None}).json()["prices"]
    assert [p["packaging_id"] for p in cleared["item_prices"]] == [setup["can"]]


def test_mode_change_copies_to_lots_and_back_needs_explicit_prices(
    owner: Client, cmp: str, setup: dict[str, Any]
) -> None:
    put(owner, url(cmp, setup["oil"], "price"), {"packaging_id": setup["can"], "amount": "1200"})
    to_lots = post(owner, url(cmp, setup["oil"], "price-mode"), {"mode": "lot"}, key()).json()[
        "prices"
    ]
    assert to_lots["mode"] == "lot" and to_lots["item_prices"] == []
    lot = Lot.objects.get()
    assert [p["amount"] for p in to_lots["lots"][0]["prices"]] == ["1200.00"]
    no_lot = put(owner, url(cmp, setup["oil"], "price"), {"amount": "300"})
    assert no_lot.json()["error"]["code"] == "PRICE_LOT_REQUIRED"
    put(
        owner,
        url(cmp, setup["oil"], "price"),
        {"lot_id": str(lot.id), "packaging_id": setup["can"], "amount": "1300"},
    )
    oil = Item.objects.get(pk=setup["oil"])
    assert prices.price_for(
        oil, lot=lot, packaging=Packaging.objects.get(pk=setup["can"])
    ) == Decimal("1300")

    back = post(
        owner,
        url(cmp, setup["oil"], "price-mode"),
        {"mode": "item", "prices": [{"packaging_id": setup["can"], "amount": "1250"}]},
        key(),
    ).json()["prices"]
    assert back["mode"] == "item" and [p["amount"] for p in back["item_prices"]] == ["1250.00"]
    assert back["lots"][0]["prices"] == []


def test_own_price_of_a_unit_and_rights(owner: Client, cmp: str, setup: dict[str, Any]) -> None:
    put(owner, url(cmp, setup["pads"], "price"), {"amount": "900"})
    asset = Asset.objects.first()
    assert asset is not None
    done = put(owner, f"/api/c/{cmp}/warehouse/assets/{asset.id}/price", {"amount": "750"})
    assert done.status_code == 200, done.content
    pads = Item.objects.get(pk=setup["pads"])
    asset.refresh_from_db()
    assert prices.price_for(pads, asset=asset) == Decimal("750")
    assert asset.purchase_cost == Decimal("500")
    mode = post(owner, url(cmp, setup["pads"], "price-mode"), {"mode": "lot"}, key())
    assert mode.json()["error"]["code"] == "PRICE_MODE_FOR_MATERIALS_ONLY"

    grant = {"type": "grant", "locations": "all", "contexts": "all", "cash_accounts": "all"}
    pricer, _ = hire(
        owner, cmp, "pricer@example.com", [{**grant, "permission": "warehouse.price.set"}]
    )
    assert put(pricer, url(cmp, setup["oil"], "price"), {"amount": "300"}).status_code == 200
    assert (
        post(pricer, url(cmp, setup["oil"], "price-mode"), {"mode": "lot"}, key()).status_code
        == 403
    )


def test_vehicle_price_only_in_a_warehouse(owner: Client, cmp: str, setup: dict[str, Any]) -> None:
    car = vehicle(owner, cmp, setup["location"])
    refused = put(owner, f"/api/c/{cmp}/warehouse/vehicles/{car}/price", {"amount": "350000"})
    assert refused.json()["error"]["code"] == "VEHICLE_NOT_IN_WAREHOUSE"


def test_minimum_stock_warns(owner: Client, cmp: str, setup: dict[str, Any]) -> None:
    set_min = put(
        owner, url(cmp, setup["oil"], "minimum"), {"context_id": setup["ctx"], "quantity": "20"}
    )
    assert set_min.status_code == 200, set_min.content
    whole = put(
        owner, url(cmp, setup["pads"], "minimum"), {"context_id": setup["ctx"], "quantity": "1.5"}
    )
    assert whole.json()["error"]["code"] == "QUANTITY_WHOLE_UNITS"
    put(owner, url(cmp, setup["pads"], "minimum"), {"context_id": setup["ctx"], "quantity": "1"})

    low = owner.get(f"/api/c/{cmp}/warehouse/low-stock").json()["low_stock"]
    assert [(row["item"]["name"], row["available"], row["minimum"]) for row in low] == [
        ("Oil", "12.00", "20.00")
    ]
    stock = {
        row["item"]["name"]: row
        for row in owner.get(f"/api/c/{cmp}/warehouse/stock").json()["stock"]
    }
    assert stock["Oil"]["below_minimum"] is True and stock["Pads"]["below_minimum"] is False
    put(owner, url(cmp, setup["oil"], "minimum"), {"context_id": setup["ctx"], "quantity": None})
    assert owner.get(f"/api/c/{cmp}/warehouse/low-stock").json()["low_stock"] == []
