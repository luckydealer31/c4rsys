"""XLSX import of initial stock (ТЗ § 22.4.2, § 22.2.2, § 22.2.5; block 6, part К3)."""

import io
from decimal import Decimal
from typing import Any

import pytest
from django.test import Client
from openpyxl import load_workbook

from c4rsys.warehouse.models import (
    Asset,
    Lot,
    LotStock,
    OpenedPackage,
    Packaging,
    Receipt,
)
from tests.accounts.helpers import post
from tests.companies.helpers import create_company, key
from tests.warehouse.test_import_data import checked, run, template
from tests.warehouse.test_receipts import item, warehouse

pytestmark = pytest.mark.django_db


@pytest.fixture
def cmp(owner: Client) -> str:
    public_id: str = create_company(owner, timezone="Asia/Bangkok", main_currency="THB").json()[
        "company"
    ]["public_id"]
    return public_id


@pytest.fixture
def setup(owner: Client, cmp: str) -> dict[str, Any]:
    ctx = warehouse(owner, cmp)
    pads = item(owner, cmp, "Brake pads", "piece")
    oil = item(owner, cmp, "Engine oil", "liter")
    packed = post(
        owner,
        f"/api/c/{cmp}/warehouse/items/{oil['id']}/packagings",
        {"quantity": "4", "name": "Canister 4 l"},
        key(),
    )
    assert packed.status_code == 201, packed.content
    return {"ctx": ctx, "pads": pads["id"], "oil": oil["id"]}


def decide(owner: Client, cmp: str, operation: dict[str, Any], *pairs: tuple[str, str]) -> Any:
    return run(
        owner,
        cmp,
        operation,
        "decisions",
        decisions=[{"row": row, "action": action, "target": ""} for row, action in pairs],
    )


def test_units_and_lots_are_imported(owner: Client, cmp: str, setup: dict[str, Any]) -> None:
    ctx, pads, oil = setup["ctx"], setup["pads"], setup["oil"]
    book = load_workbook(io.BytesIO(template(owner, cmp, "stock")))
    assert book["contexts"]["A2"].value == ctx  # the warehouses of the company
    assert {book["items"][f"A{n}"].value for n in (2, 3)} == {pads, oil}
    lot = {"item_id": oil, "context_id": ctx}
    a1 = {**lot, "lot_code": "A1", "lot_external_number": "B-77", "expiry_date": "2027-01-01"}
    operation = checked(
        owner,
        cmp,
        "stock",
        [
            {"item_id": pads, "context_id": ctx, "serial_number": "SN-1", "unit_cost": "450"},
            {"item_id": pads, "context_id": ctx, "place_tags": "Shelf 2, Box 1"},
            {**a1, "packaging_quantity": "4", "packs": "3", "unit_cost": "250"},
            {**a1, "packaging_quantity": "4", "opened": "yes", "quantity": "2.5"},
            {**lot, "lot_code": "A2", "quantity": "7.5"},
            {"item_id": pads, "context_id": ctx, "quantity": "2"},
            {**lot, "lot_code": "A3", "lot_id": "LOT-1", "quantity": "1"},
            {**lot, "context_id": "00000000-0000-0000-0000-000000000000", "lot_code": "A4"},
            {**a1, "packaging_quantity": "4", "packs": "1", "quantity": "5"},
        ],
    )
    rows = operation["rows"]
    assert [r["action"] for r in rows] == [
        "create",
        "create",
        "create",  # the group A1 has a wrong row: it is held back at posting
        "create",
        "create",
        "error",
        "error",
        "error",
        "error",
    ]
    assert rows[5]["errors"] == [{"field": "quantity", "code": "IMPORT_ONE_UNIT_PER_ROW"}]
    assert {"field": "lot_id", "code": "LOT_CODE_AND_LOT_ID"} in rows[6]["errors"]
    assert {"field": "context_id", "code": "WAREHOUSE_NOT_USABLE"} in rows[7]["errors"]
    assert rows[8]["errors"] == [{"field": "quantity", "code": "QUANTITY_NOT_PACKS"}]

    # Without the wrong row the group A1 is loaded as a whole.
    operation = checked(
        owner,
        cmp,
        "stock",
        [
            {"item_id": pads, "context_id": ctx, "serial_number": "SN-1", "unit_cost": "450"},
            {"item_id": pads, "context_id": ctx, "place_tags": "Shelf 2, Box 1"},
            {**a1, "packaging_quantity": "4", "packs": "3", "unit_cost": "250"},
            {**a1, "packaging_quantity": "4", "opened": "yes", "quantity": "2.5"},
            {**lot, "lot_code": "A2", "quantity": "7.5"},
        ],
    )
    assert [r["action"] for r in operation["rows"]] == ["create"] * 5
    posted = run(owner, cmp, operation, "post")
    assert posted.status_code == 200, posted.content
    body = posted.json()["import"]
    assert [r["result"] for r in body["rows"]] == ["created"] * 5

    receipt = Receipt.objects.get()
    assert (receipt.source, receipt.state, receipt.supplier_id) == ("initial", "posted", None)
    assert str(receipt.import_operation_id) == body["id"]
    units = list(Asset.objects.order_by("created_at"))
    assert [u.serial_number for u in units] == ["SN-1", ""]
    assert units[0].purchase_cost == Decimal("450") and units[1].tags == ["Shelf 2", "Box 1"]
    assert all(u.public_id.startswith("AST") for u in units)
    first = Lot.objects.get(external_number="B-77")  # one LOT ID for the group
    assert str(first.expiry_date) == "2027-01-01"
    packed = Packaging.objects.get(item_id=oil)
    assert LotStock.objects.get(lot=first, packaging=packed).quantity == Decimal("12.00")
    assert OpenedPackage.objects.get(lot=first).remaining == Decimal("2.50")
    loose = Lot.objects.exclude(pk=first.pk).get()
    assert LotStock.objects.get(lot=loose, packaging=None).quantity == Decimal("7.50")

    # More stock into the existing lot by its LOT ID; cancel takes only what it added.
    more = checked(
        owner,
        cmp,
        "stock",
        [{**lot, "lot_id": loose.public_id, "quantity": "2.5"}],
    )
    assert more["rows"][0]["action"] == "create"
    added = run(owner, cmp, more, "post").json()["import"]
    assert LotStock.objects.get(lot=loose, packaging=None).quantity == Decimal("10.00")
    cancelled = run(owner, cmp, added, "cancel")
    assert cancelled.status_code == 200, cancelled.content
    assert LotStock.objects.get(lot=loose, packaging=None).quantity == Decimal("7.50")

    # The first import is cancelled while nothing of it was used.
    used = Asset.objects.filter(serial_number="SN-1")
    used.update(state="issued")
    blocked = run(owner, cmp, body, "cancel")
    assert blocked.json()["error"]["code"] == "IMPORT_CANCEL_BLOCKED"
    assert blocked.json()["error"]["params"]["blockers"][0]["type"] == "warehouse_asset"
    used.update(state="in_stock")
    done = run(owner, cmp, body, "cancel")
    assert done.status_code == 200, done.content
    assert set(Asset.objects.values_list("state", flat=True)) == {"cancelled"}
    assert LotStock.objects.get(lot=first, packaging=packed).quantity == Decimal("0.00")
    assert OpenedPackage.objects.get(lot=first).remaining == Decimal("0.00")


def test_group_rules(owner: Client, cmp: str, setup: dict[str, Any]) -> None:
    ctx, oil = setup["ctx"], setup["oil"]
    lot = {"item_id": oil, "context_id": ctx}
    operation = checked(
        owner,
        cmp,
        "stock",
        [
            {**lot, "lot_code": "B1", "expiry_date": "2027-01-01", "quantity": "1"},
            {**lot, "lot_code": "B1", "quantity": "1"},  # an empty value against a filled one
            {**lot, "lot_code": "C1", "quantity": "1"},
            {**lot, "lot_code": "C1", "packaging_quantity": "5", "packs": "1"},
        ],
    )
    rows = operation["rows"]
    mismatch = {"field": "expiry_date", "code": "IMPORT_GROUP_MISMATCH"}
    assert mismatch in rows[0]["errors"] and mismatch in rows[1]["errors"]
    # A packaging that does not exist yet is shown and waits for a confirmation (§ 22.4.2).
    assert rows[3]["extra"]["packaging_missing"]["quantity"] == "5.00"
    assert [rows[2]["action"], rows[3]["action"]] == ["create", "skip"]
    refused = decide(owner, cmp, operation, ("R0004", "create"))
    assert refused.json()["error"]["code"] == "IMPORT_PACKAGING_CONFIRM"
    # A skipped row of a group keeps the whole group back.
    posted = run(owner, cmp, operation, "post").json()["import"]
    assert posted["rows"][2]["result"] == "conflict"
    assert posted["rows"][2]["errors"] == [{"field": "lot_code", "code": "IMPORT_GROUP_INCOMPLETE"}]
    assert not Lot.objects.exists() and not Packaging.objects.filter(quantity=5).exists()

    again = checked(
        owner,
        cmp,
        "stock",
        [
            {**lot, "lot_code": "C1", "quantity": "1"},
            {**lot, "lot_code": "C1", "packaging_quantity": "5", "packs": "2"},
        ],
    )
    # Skipping one row of a group excludes the whole group; choosing to load brings it back.
    skipped = decide(owner, cmp, again, ("R0001", "skip")).json()["import"]
    assert [r["action"] for r in skipped["rows"]] == ["skip", "skip"]
    confirmed = decide(
        owner, cmp, skipped, ("R0001", "create"), ("R0002", "create_packaging")
    ).json()["import"]
    assert [r["action"] for r in confirmed["rows"]] == ["create", "create_packaging"]
    done = run(owner, cmp, confirmed, "post").json()["import"]
    assert [r["result"] for r in done["rows"]] == ["created", "created"]
    new = Packaging.objects.get(quantity=5)
    created = Lot.objects.get()
    assert LotStock.objects.get(lot=created, packaging=new).quantity == Decimal("10.00")


def test_external_number_and_prices(owner: Client, cmp: str, setup: dict[str, Any]) -> None:
    ctx, pads, oil = setup["ctx"], setup["pads"], setup["oil"]
    lot = {"item_id": oil, "context_id": ctx}
    d1 = {**lot, "lot_code": "D1", "lot_external_number": "X-1"}
    first = checked(owner, cmp, "stock", [{**d1, "quantity": "1"}])
    run(owner, cmp, first, "post")
    operation = checked(
        owner,
        cmp,
        "stock",
        [
            {**lot, "lot_code": "D1", "lot_external_number": "X-1", "quantity": "2"},
            {"item_id": pads, "context_id": ctx, "sale_price": "990"},
            {**lot, "lot_code": "E1", "quantity": "1", "sale_price": "100"},
        ],
    )
    rows = operation["rows"]
    # The same external number shows only a possible existing lot (§ 22.2.3).
    assert rows[0]["matches"][0]["by"] == "lot_external_number" and rows[0]["action"] == "skip"
    linked = decide(owner, cmp, operation, ("R0001", "link"))
    assert linked.json()["error"]["code"] == "IMPORT_ACTION_UNKNOWN"
    assert rows[2]["errors"] == [{"field": "sale_price", "code": "PRICE_ITEM_MODE"}]
    decided = decide(owner, cmp, operation, ("R0001", "create_duplicate")).json()["import"]
    posted = run(owner, cmp, decided, "post").json()["import"]
    assert [r["result"] for r in posted["rows"]] == ["created", "created", "skipped"]
    assert Lot.objects.filter(external_number="X-1").count() == 2
    assert Asset.objects.get().sale_price == Decimal("990")


def test_wrong_row_holds_its_group(owner: Client, cmp: str, setup: dict[str, Any]) -> None:
    lot = {"item_id": setup["oil"], "context_id": setup["ctx"], "lot_code": "F1"}
    operation = checked(
        owner,
        cmp,
        "stock",
        [
            {**lot, "quantity": "1"},
            {**lot, "packaging_quantity": "4", "packs": "1", "quantity": "5"},
            {"item_id": setup["pads"], "context_id": setup["ctx"]},  # an independent row
        ],
    )
    posted = run(owner, cmp, operation, "post").json()["import"]
    assert [r["result"] for r in posted["rows"]] == ["conflict", "skipped", "created"]
    incomplete = [{"field": "lot_code", "code": "IMPORT_GROUP_INCOMPLETE"}]
    assert posted["rows"][0]["errors"] == incomplete
    assert not Lot.objects.exists() and Asset.objects.count() == 1
