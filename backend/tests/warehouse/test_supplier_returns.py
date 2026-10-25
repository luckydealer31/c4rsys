"""Warehouse, part С7: return to the supplier by the original receipt (ТЗ § 11.4.2)."""

from decimal import Decimal
from typing import Any

import pytest
from django.test import Client

from c4rsys.warehouse.models import Asset, LotLayer, LotStock
from tests.accounts.helpers import post
from tests.companies.helpers import create_company, key
from tests.warehouse.test_operations import item as new_item
from tests.warehouse.test_operations import location, packaging, warehouse_of

pytestmark = pytest.mark.django_db


@pytest.fixture
def cmp(owner: Client) -> str:
    public_id: str = create_company(owner, timezone="Asia/Bangkok", main_currency="THB").json()[
        "company"
    ]["public_id"]
    return public_id


def receipt(
    owner: Client, cmp: str, ctx: str, source: str, lines: list[dict[str, Any]]
) -> dict[str, Any]:
    draft = post(
        owner,
        f"/api/c/{cmp}/warehouse/receipts",
        {"source": source, "context_id": ctx, "lines": lines},
        key(),
    ).json()["receipt"]
    url = f"/api/c/{cmp}/warehouse/receipts/{draft['id']}/post"
    posted: dict[str, Any] = post(owner, url, {"expected_version": draft["version"]}, key()).json()[
        "receipt"
    ]
    return posted


def give_back(owner: Client, cmp: str, receipt_id: str, lines: list[dict[str, Any]]) -> Any:
    return post(
        owner,
        f"/api/c/{cmp}/warehouse/receipts/{receipt_id}/return",
        {"lines": lines, "comment": "Defect"},
        key(),
    )


def test_return_takes_from_the_receipt_itself(owner: Client, cmp: str) -> None:
    ctx = warehouse_of(location(owner, cmp, "Phuket"))
    oil = new_item(owner, cmp, "Oil", "liter")
    can = packaging(owner, cmp, oil["id"], "4")
    pads = new_item(owner, cmp, "Pads", "piece")
    first = receipt(
        owner,
        cmp,
        ctx,
        "purchase",
        [{"item_id": oil["id"], "packaging_id": can, "packs": 2, "unit_cost": "100"}],
    )
    lot_id = first["lines"][0]["lot"]["id"]
    second = receipt(
        owner,
        cmp,
        ctx,
        "purchase",
        [
            {
                "item_id": oil["id"],
                "packaging_id": can,
                "packs": 1,
                "unit_cost": "200",
                "lot_id": lot_id,
            },
            {"item_id": pads["id"], "quantity": 2, "unit_cost": "500"},
        ],
    )
    oil_line, pad_line = second["lines"]

    too_much = give_back(
        owner,
        cmp,
        second["id"],
        [{"receipt_line_id": oil_line["id"], "packaging_id": can, "quantity": "8"}],
    )
    assert too_much.json()["error"]["code"] == "RETURN_EXCEEDS_RECEIPT"
    pad = Asset.objects.filter(receipt_line_id=pad_line["id"]).first()
    assert pad is not None
    done = give_back(
        owner,
        cmp,
        second["id"],
        [
            {"receipt_line_id": oil_line["id"], "packaging_id": can, "quantity": "4"},
            {"receipt_line_id": pad_line["id"], "asset_ids": [str(pad.id)]},
        ],
    )
    assert done.status_code == 201, done.content
    body = done.json()["operation"]
    assert body["kind"] == "supplier_return" and body["receipt"]["number"] == second["number"]
    assert body["lines"][0]["cost"] == "800.00"

    # § 11.2.13: the second receipt's own layer goes down, not the earlier one.
    layers = {str(layer.receipt_line_id): layer.remaining for layer in LotLayer.objects.all()}
    assert layers[first["lines"][0]["id"]] == Decimal("8.00")
    assert layers[oil_line["id"]] == Decimal("0.00")
    assert LotStock.objects.get(packaging_id=can).quantity == Decimal("8.00")
    pad.refresh_from_db()
    assert pad.state == "returned"

    again = owner.get(f"/api/c/{cmp}/warehouse/receipts/{second['id']}").json()["receipt"]
    by_id = {line["id"]: line for line in again["lines"]}
    assert by_id[oil_line["id"]]["returned_quantity"] == "4.00"
    assert by_id[pad_line["id"]]["returnable_quantity"] == "1"


def test_only_purchase_receipts_return(owner: Client, cmp: str) -> None:
    ctx = warehouse_of(location(owner, cmp, "Phuket"))
    pads = new_item(owner, cmp, "Pads", "piece")
    initial = receipt(owner, cmp, ctx, "initial", [{"item_id": pads["id"], "quantity": 1}])
    pad = Asset.objects.get()
    refused = give_back(
        owner,
        cmp,
        initial["id"],
        [{"receipt_line_id": initial["lines"][0]["id"], "asset_ids": [str(pad.id)]}],
    )
    assert refused.json()["error"]["code"] == "RETURN_PURCHASE_RECEIPTS_ONLY"
