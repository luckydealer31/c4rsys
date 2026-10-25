"""Warehouse, part С4: inventory of chosen items, discrepancies at the moment of the count,
confirmation with a separate right, rejection and resubmission, found units (ТЗ § 11.2.1,
§ 11.6; decisions of 25.10.2026)."""

from decimal import Decimal
from typing import Any

import pytest
from django.test import Client

from c4rsys.warehouse.models import Asset, LotLayer, LotStock, Receipt
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


def base(cmp: str, count: dict[str, Any] | None = None) -> str:
    url = f"/api/c/{cmp}/warehouse/inventories"
    return f"{url}/{count['id']}" if count else url


def new_count(client: Client, cmp: str, ctx: str) -> dict[str, Any]:
    response = post(client, base(cmp), {"context_id": ctx}, key())
    assert response.status_code == 201, response.content
    count: dict[str, Any] = response.json()["inventory"]
    return count


def count_lines(client: Client, cmp: str, count: dict[str, Any], values: dict[str, Any]) -> Any:
    lines = [{"line_id": line_id, "quantity": q} for line_id, q in values.items()]
    return post(client, f"{base(cmp, count)}/counted", {"lines": lines}, key())


def test_count_confirm_applies_discrepancies(
    owner: Client, cmp: str, setup: dict[str, Any]
) -> None:
    count = new_count(owner, cmp, setup["ctx"])
    count = post(owner, f"{base(cmp, count)}/items", {"all": True}, key()).json()["inventory"]
    kinds = sorted(line["kind"] for line in count["lines"])
    assert kinds == ["asset", "asset", "lot"]
    asset_lines = [line for line in count["lines"] if line["kind"] == "asset"]
    lot_line = next(line for line in count["lines"] if line["kind"] == "lot")

    refused = count_lines(owner, cmp, count, {lot_line["id"]: "6"})
    assert refused.json()["error"]["code"] == "QUANTITY_NOT_WHOLE_PACKS"
    counted = count_lines(
        owner, cmp, count, {lot_line["id"]: "8", asset_lines[0]["id"]: 1, asset_lines[1]["id"]: 0}
    ).json()["inventory"]
    line = next(entry for entry in counted["lines"] if entry["id"] == lot_line["id"])
    assert (line["book_quantity"], line["discrepancy"]) == ("12.00", "-4.00")
    # § 11.6.1: the count itself does not change the stock.
    assert LotStock.objects.get().quantity == Decimal("12.00")

    submitted = post(owner, f"{base(cmp, count)}/submit", {}, key()).json()["inventory"]
    assert submitted["state"] == "submitted"
    assert (
        post(owner, f"{base(cmp, count)}/confirm", {}, key()).json()["error"]["field"] == "reason"
    )
    confirmed = post(owner, f"{base(cmp, count)}/confirm", {"reason": "Annual count"}, key())
    assert confirmed.status_code == 200, confirmed.content
    body = confirmed.json()["inventory"]
    assert body["state"] == "confirmed" and body["decided_by"] == "Anna Petrova"
    assert LotStock.objects.get().quantity == Decimal("8.00")
    assert sorted(Asset.objects.values_list("state", flat=True)) == ["in_stock", "written_off"]


def test_discrepancy_is_applied_to_the_current_stock(
    owner: Client, cmp: str, setup: dict[str, Any]
) -> None:
    count = new_count(owner, cmp, setup["ctx"])
    count = post(owner, f"{base(cmp, count)}/items", {"item_ids": [setup["oil"]]}, key()).json()[
        "inventory"
    ]
    lot_line = count["lines"][0]
    count_lines(owner, cmp, count, {lot_line["id"]: "8"})
    post(owner, f"{base(cmp, count)}/submit", {}, key())
    # § 11.6.4: later movements stay — 4 l are used after the count.
    car = vehicle(owner, cmp, setup["location"])
    lot_id = str(LotStock.objects.get().lot_id)
    used = post(
        owner,
        f"/api/c/{cmp}/warehouse/operations/consume",
        {
            "context_id": setup["ctx"],
            "vehicle_id": car,
            "lines": [
                {
                    "item_id": setup["oil"],
                    "lot_id": lot_id,
                    "packaging_id": setup["can"],
                    "quantity": "4",
                }
            ],
        },
        key(),
    )
    assert used.status_code == 201, used.content
    post(owner, f"{base(cmp, count)}/confirm", {"reason": "Count"}, key())
    assert LotStock.objects.get().quantity == Decimal("4.00")

    again = new_count(owner, cmp, setup["ctx"])
    again = post(owner, f"{base(cmp, again)}/items", {"all": True}, key()).json()["inventory"]
    oil_line = next(line for line in again["lines"] if line["kind"] == "lot")
    pads = {line["id"]: 1 for line in again["lines"] if line["kind"] == "asset"}
    count_lines(owner, cmp, again, {oil_line["id"]: "0", **pads})
    post(owner, f"{base(cmp, again)}/submit", {}, key())
    LotStock.objects.update(quantity=Decimal("0.00"))  # the stock left meanwhile
    blocked = post(owner, f"{base(cmp, again)}/confirm", {"reason": "Count"}, key())
    assert blocked.json()["error"]["code"] == "INVENTORY_RECHECK"


def test_surplus_has_no_cost_and_found_units(
    owner: Client, cmp: str, setup: dict[str, Any]
) -> None:
    count = new_count(owner, cmp, setup["ctx"])
    count = post(owner, f"{base(cmp, count)}/items", {"item_ids": [setup["oil"]]}, key()).json()[
        "inventory"
    ]
    count_lines(owner, cmp, count, {count["lines"][0]["id"]: "16"})
    found = post(
        owner,
        f"{base(cmp, count)}/found",
        {
            "kind": "found_unit",
            "item_id": setup["pads"],
            "description": "Pads in a box",
            "identifiers": "SN-9",
        },
        key(),
    )
    assert found.status_code == 200, found.content
    no_text = post(owner, f"{base(cmp, count)}/found", {"kind": "found_vehicle"}, key())
    assert no_text.json()["error"]["field"] == "description"
    pad = Asset.objects.first()
    assert pad is not None
    here = post(
        owner,
        f"{base(cmp, count)}/found",
        {"kind": "found_elsewhere", "asset_public_id": pad.public_id},
        key(),
    )
    assert here.json()["error"]["code"] == "ASSET_IS_HERE"

    post(owner, f"{base(cmp, count)}/submit", {}, key())
    post(owner, f"{base(cmp, count)}/confirm", {"reason": "Count"}, key())
    surplus = LotLayer.objects.get(inventory_line__isnull=False)
    assert surplus.unit_cost is None and surplus.received_quantity == Decimal("4.00")
    receipt = Receipt.objects.get(source="inventory")
    assert receipt.state == "posted"
    assert Asset.objects.filter(serial_number="SN-9", state="in_stock").exists()


def test_rights_rejection_and_resubmission(owner: Client, cmp: str, setup: dict[str, Any]) -> None:
    def grant(code: str) -> dict[str, Any]:
        return {
            "type": "grant",
            "permission": code,
            "locations": "all",
            "contexts": "all",
            "cash_accounts": "all",
        }

    counter, _ = hire(
        owner, cmp, "counter@example.com", [grant("warehouse.view"), grant("warehouse.count")]
    )
    count = new_count(counter, cmp, setup["ctx"])
    count = post(counter, f"{base(cmp, count)}/items", {"item_ids": [setup["oil"]]}, key()).json()[
        "inventory"
    ]
    count_lines(counter, cmp, count, {count["lines"][0]["id"]: "8"})
    post(counter, f"{base(cmp, count)}/submit", {}, key())
    assert post(counter, f"{base(cmp, count)}/confirm", {"reason": "x"}, key()).status_code == 403

    rejected = post(owner, f"{base(cmp, count)}/reject", {"reason": "Recount the shelf"}, key())
    assert rejected.json()["inventory"]["state"] == "rejected"
    assert LotStock.objects.get().quantity == Decimal("12.00")
    count_lines(counter, cmp, count, {count["lines"][0]["id"]: "12"})
    assert (
        post(counter, f"{base(cmp, count)}/submit", {}, key()).json()["inventory"]["state"]
        == "submitted"
    )
    history = owner.get(base(cmp)).json()["inventories"]
    assert [c["state"] for c in history] == ["submitted"]
