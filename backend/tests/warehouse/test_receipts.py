"""Warehouse, part С2: receipts, lots and assets, purchases, late cost, supplier articles
(ТЗ § 11.1.2–11.1.4, § 11.1.9–11.1.10, § 11.2; customer's decisions of 25.10.2026)."""

from decimal import Decimal
from typing import Any

import pytest
from django.db import connection
from django.test import Client

from c4rsys.platform.models import AuditEvent
from c4rsys.warehouse.models import Asset, Lot, LotStock, StockMovement
from tests.accounts.helpers import post
from tests.companies.helpers import create_company, hire, key

pytestmark = pytest.mark.django_db


@pytest.fixture
def cmp(owner: Client) -> str:
    public_id: str = create_company(owner, timezone="Asia/Bangkok", main_currency="THB").json()[
        "company"
    ]["public_id"]
    return public_id


def location(owner: Client, cmp: str, name: str, *contexts: str) -> dict[str, Any]:
    response = post(
        owner, f"/api/c/{cmp}/locations", {"name": name, "contexts": list(contexts)}, key()
    )
    assert response.status_code == 201, response.content
    created: dict[str, Any] = response.json()["location"]
    return created


def warehouse(owner: Client, cmp: str, name: str = "Phuket") -> str:
    created = location(owner, cmp, name, "warehouse", "rental")
    return next(str(c["id"]) for c in created["contexts"] if c["type"] == "warehouse")


def item(owner: Client, cmp: str, name: str, unit: str) -> dict[str, Any]:
    response = post(owner, f"/api/c/{cmp}/warehouse/items", {"name": name, "unit": unit}, key())
    assert response.status_code == 201, response.content
    created: dict[str, Any] = response.json()["item"]
    return created


def supplier(owner: Client, cmp: str) -> str:
    body = {
        "kind": "company",
        "company_name": "Auto Parts Co",
        "contacts": [{"kind": "email", "value": "sales@parts.example"}],
    }
    return str(
        post(owner, f"/api/c/{cmp}/counterparties", body, key()).json()["counterparty"]["id"]
    )


def place_of(owner: Client, cmp: str, ctx: str, name: str) -> str:
    """Makes a storage place of the warehouse and gives the warehouse back."""
    made = post(owner, f"/api/c/{cmp}/warehouse/places", {"context_id": ctx, "name": name}, key())
    assert made.status_code == 201, made.content
    return ctx


def receipt(owner: Client, cmp: str, **data: Any) -> Any:
    return post(owner, f"/api/c/{cmp}/warehouse/receipts", {"source": "purchase", **data}, key())


def post_receipt(owner: Client, cmp: str, created: dict[str, Any]) -> Any:
    url = f"/api/c/{cmp}/warehouse/receipts/{created['id']}/post"
    return post(owner, url, {"expected_version": created["version"]}, key())


def test_pieces_get_assets_and_supplier_may_stay_unknown(owner: Client, cmp: str) -> None:
    ctx = warehouse(owner, cmp)
    pads = item(owner, cmp, "Brake pads", "piece")
    draft = receipt(
        owner,
        cmp,
        context_id=place_of(owner, cmp, ctx, "Shelf A"),
        lines=[
            {
                "item_id": pads["id"],
                "quantity": 2,
                "unit_cost": "450.50",
                "serial_numbers": ["SN-1"],
                "tags": ["Shelf A"],
            }
        ],
    )
    assert draft.status_code == 201, draft.content
    body = draft.json()["receipt"]
    assert body["state"] == "draft" and body["supplier"] is None
    assert body["total_cost"] == "901.00"
    # A draft does not change the stock.
    assert not Asset.objects.exists()

    posted = post_receipt(owner, cmp, body)
    assert posted.status_code == 200, posted.content
    assets = posted.json()["receipt"]["lines"][0]["assets"]
    assert len(assets) == 2 and all(a.startswith("AST-") and len(a) == 16 for a in assets)
    assert sorted(Asset.objects.values_list("serial_number", flat=True)) == ["", "SN-1"]
    assert StockMovement.objects.count() == 2

    stock = owner.get(f"/api/c/{cmp}/warehouse/stock").json()["stock"]
    assert stock[0]["quantity"] == "2.00" and stock[0]["tags"] == ["Shelf A"]
    expanded = owner.get(f"/api/c/{cmp}/warehouse/items/{pads['id']}/stock").json()["stock"]
    assert expanded["places"][0]["assets"][0]["purchase_cost"] == "450.50"

    again = post_receipt(owner, cmp, posted.json()["receipt"])
    assert again.json()["error"]["code"] == "RECEIPT_ALREADY_POSTED"


def test_lot_with_packagings_layers_and_unknown_cost(owner: Client, cmp: str) -> None:
    ctx = warehouse(owner, cmp)
    oil = item(owner, cmp, "Oil 5W-30", "liter")
    pack = post(
        owner, f"/api/c/{cmp}/warehouse/items/{oil['id']}/packagings", {"quantity": "4"}, key()
    ).json()["item"]["packagings"][0]
    first = receipt(
        owner,
        cmp,
        context_id=ctx,
        source="initial",
        lines=[
            {
                "item_id": oil["id"],
                "packaging_id": pack["id"],
                "packs": 3,
                "lot_external_number": "B-77",
                "lot_expiry_date": "2027-05-01",
            }
        ],
    ).json()["receipt"]
    # § 11.2.6: without a cost the receipt is allowed; it is not zero.
    assert first["cost_incomplete"] is True and first["lines"][0]["unit_cost"] is None
    posted = post_receipt(owner, cmp, first).json()["receipt"]
    lot = Lot.objects.get()
    assert lot.public_id.startswith("LOT-") and lot.external_number == "B-77"
    assert LotStock.objects.get().quantity == Decimal("12.00")

    # § 11.2.16: the same external number shows the existing lot as a possible match.
    lots = owner.get(
        f"/api/c/{cmp}/warehouse/items/{oil['id']}/lots?context_id={ctx}&external_number=b-77"
    ).json()["lots"]
    assert lots[0]["matches"] is True

    # § 11.2.14: a second receipt adds to the same lot with its own cost.
    second = receipt(
        owner,
        cmp,
        context_id=ctx,
        lines=[
            {"item_id": oil["id"], "quantity": "1.5", "unit_cost": "300", "lot_id": str(lot.id)}
        ],
    ).json()["receipt"]
    assert post_receipt(owner, cmp, second).status_code == 200
    expanded = owner.get(f"/api/c/{cmp}/warehouse/items/{oil['id']}/stock").json()["stock"]
    entry = expanded["places"][0]["lots"][0]
    assert entry["quantity"] == "13.50"
    assert [s["packs"] for s in entry["stock"]] == [3, None]
    assert [layer["unit_cost"] for layer in entry["layers"]] == [None, "300.00"]

    # § 11.2.6: the unknown cost is entered later, with history.
    line = posted["lines"][0]["id"]
    url = f"/api/c/{cmp}/warehouse/receipts/{posted['id']}/lines/{line}/cost"
    entered = post(owner, url, {"unit_cost": "250"}, key())
    assert entered.json()["receipt"]["cost_incomplete"] is False
    assert (
        post(owner, url, {"unit_cost": "260"}, key()).json()["error"]["code"]
        == "COST_ALREADY_GIVEN"
    )
    assert AuditEvent.objects.filter(action="warehouse.receipt_cost_entered").count() == 1


def test_quantity_rules(owner: Client, cmp: str) -> None:
    ctx = warehouse(owner, cmp)
    pads = item(owner, cmp, "Brake pads", "piece")
    oil = item(owner, cmp, "Oil", "liter")
    half = receipt(owner, cmp, context_id=ctx, lines=[{"item_id": pads["id"], "quantity": "1.5"}])
    assert half.json()["error"]["code"] == "QUANTITY_WHOLE_UNITS"
    precise = receipt(
        owner, cmp, context_id=ctx, lines=[{"item_id": oil["id"], "quantity": "1.555"}]
    )
    assert precise.json()["error"]["code"] == "QUANTITY_TOO_PRECISE"
    cost = receipt(
        owner,
        cmp,
        context_id=ctx,
        lines=[{"item_id": oil["id"], "quantity": "1", "unit_cost": "1.005"}],
    )
    assert cost.json()["error"]["code"] == "AMOUNT_TOO_PRECISE"
    initial = receipt(
        owner,
        cmp,
        context_id=ctx,
        source="initial",
        supplier_id=supplier(owner, cmp),
        lines=[{"item_id": oil["id"], "quantity": "1"}],
    )
    assert initial.json()["error"]["code"] == "INITIAL_WITHOUT_SUPPLIER"


def test_only_a_warehouse_receives_and_a_draft_is_changed_or_deleted(
    owner: Client, cmp: str
) -> None:
    rental_only = location(owner, cmp, "Krabi", "rental")
    oil = item(owner, cmp, "Oil", "liter")
    lines = [{"item_id": oil["id"], "quantity": "2"}]
    refused = receipt(owner, cmp, context_id=rental_only["contexts"][0]["id"], lines=lines)
    assert refused.status_code == 404

    ctx = warehouse(owner, cmp)
    draft = receipt(owner, cmp, context_id=ctx, lines=lines).json()["receipt"]
    url = f"/api/c/{cmp}/warehouse/receipts/{draft['id']}"
    changed = owner.patch(
        url,
        data={
            "source": "purchase",
            "context_id": ctx,
            "expected_version": 1,
            "lines": [{"item_id": oil["id"], "quantity": "3", "expected_quantity": "4"}],
        },
        content_type="application/json",
        headers=key(),
    )
    assert changed.json()["receipt"]["lines"][0]["discrepancy"] == "-1.00"
    assert owner.delete(url, headers=key()).status_code == 200
    assert owner.get(url).status_code == 404


def test_purchase_partial_receipts_and_cancelled_rest(owner: Client, cmp: str) -> None:
    ctx = warehouse(owner, cmp)
    oil = item(owner, cmp, "Oil", "liter")
    seller = supplier(owner, cmp)
    purchase = post(
        owner,
        f"/api/c/{cmp}/warehouse/purchases",
        {
            "supplier_id": seller,
            "lines": [{"item_id": oil["id"], "quantity": "10", "unit_cost": "200"}],
        },
        key(),
    ).json()["purchase"]
    line = purchase["lines"][0]
    assert purchase["state"] == "open" and line["expected_quantity"] == "10.00"

    part = receipt(
        owner,
        cmp,
        context_id=ctx,
        purchase_id=purchase["id"],
        lines=[
            {
                "item_id": oil["id"],
                "quantity": "4",
                "unit_cost": "200",
                "purchase_line_id": line["id"],
            }
        ],
    ).json()["receipt"]
    assert part["supplier"]["id"] == seller
    post_receipt(owner, cmp, part)
    url = f"/api/c/{cmp}/warehouse/purchases/{purchase['id']}"
    now = owner.get(url).json()["purchase"]["lines"][0]
    assert (now["received_quantity"], now["expected_quantity"]) == ("4.00", "6.00")

    cancelled = post(owner, f"{url}/cancel-rest", {}, key()).json()["purchase"]
    assert cancelled["state"] == "completed"
    assert cancelled["lines"][0]["cancelled_quantity"] == "6.00"
    assert (
        post(owner, f"{url}/cancel-rest", {}, key()).json()["error"]["code"] == "NOTHING_EXPECTED"
    )


def test_supplier_articles(owner: Client, cmp: str) -> None:
    oil = item(owner, cmp, "Oil", "liter")
    seller = supplier(owner, cmp)
    base = f"/api/c/{cmp}/warehouse/items/{oil['id']}/supplier-articles"
    assert post(owner, base, {"supplier_id": seller, "article": "SH-530"}, key()).status_code == 201
    taken = post(owner, base, {"supplier_id": seller, "article": "SH-530"}, key())
    assert taken.json()["error"]["code"] == "SUPPLIER_ARTICLE_TAKEN"
    found = owner.get(
        f"/api/c/{cmp}/warehouse/supplier-articles/lookup?supplier_id={seller}&article=sh-530"
    )
    assert found.json()["item"]["id"] == oil["id"]


def test_rights_count_in_the_warehouse(owner: Client, cmp: str) -> None:
    first = location(owner, cmp, "Phuket", "warehouse")
    second = location(owner, cmp, "Krabi", "warehouse")
    oil = item(owner, cmp, "Oil", "liter")
    rights = [
        {
            "type": "grant",
            "permission": code,
            "locations": [first["id"]],
            "contexts": "all",
            "cash_accounts": "all",
        }
        for code in ("warehouse.view", "warehouse.receive")
    ]
    keeper, _ = hire(owner, cmp, "keeper@example.com", rights)
    lines = [{"item_id": oil["id"], "quantity": "1"}]
    assert (
        receipt(keeper, cmp, context_id=first["contexts"][0]["id"], lines=lines).status_code == 201
    )
    other = receipt(keeper, cmp, context_id=second["contexts"][0]["id"], lines=lines)
    assert other.status_code == 403


def test_movement_journal_is_append_only(owner: Client, cmp: str) -> None:
    ctx = warehouse(owner, cmp)
    pads = item(owner, cmp, "Pads", "piece")
    draft = receipt(owner, cmp, context_id=ctx, lines=[{"item_id": pads["id"], "quantity": 1}])
    post_receipt(owner, cmp, draft.json()["receipt"])
    with pytest.raises(Exception), connection.cursor() as cursor:  # noqa: B017 — the trigger
        cursor.execute("UPDATE warehouse_movement SET quantity = 5")


def test_storage_places_are_a_list_of_the_warehouse(owner: Client, cmp: str) -> None:
    """Customer's decision of 22.11.2026 (§ 11.1.9): the storage places are a list of each
    warehouse, kept by whoever manages the settings of the warehouse; the storekeeper chooses.
    A unit lies in one place; a renamed place is renamed for what lies there."""
    ctx = warehouse(owner, cmp)
    other = warehouse(owner, cmp, "Chalong")
    pads = item(owner, cmp, "Brake pads", "piece")
    keeper, _ = hire(owner, cmp, "k@example.com", [{"type": "set", "set": "storekeeper"}])
    line = {"item_id": pads["id"], "quantity": 1, "unit_cost": "100"}
    places = f"/api/c/{cmp}/warehouse/places"

    def received(by: Client, names: list[str], where: str = ctx) -> Any:
        draft = receipt(by, cmp, context_id=where, lines=[{**line, "tags": names}])
        assert draft.status_code == 201, draft.content
        return post_receipt(by, cmp, draft.json()["receipt"])

    # A place that is not in the list of the warehouse is refused, whoever receives.
    refused = received(owner, ["Rack 7"])
    assert refused.status_code == 400, refused.content
    assert refused.json()["error"]["code"] == "PLACE_TAG_UNKNOWN"
    # The list is kept with the right of the settings: not by the storekeeper.
    assert post(keeper, places, {"context_id": ctx, "name": "Rack 7"}, key()).status_code == 403
    made = post(owner, places, {"context_id": ctx, "name": "Rack 7"}, key())
    assert made.status_code == 201, made.content
    place = made.json()["place"]
    assert (
        post(owner, places, {"context_id": ctx, "name": "rack 7"}, key()).json()["error"]["code"]
        == "PLACE_NAME_TAKEN"
    )
    # Another warehouse has its own list: the same name is another place there.
    assert post(owner, places, {"context_id": other, "name": "Rack 7"}, key()).status_code == 201
    post(owner, places, {"context_id": ctx, "name": "Rack 8"}, key())
    assert sorted(p["name"] for p in keeper.get(places).json()["places"]) == [
        "Rack 7",
        "Rack 7",
        "Rack 8",
    ]

    assert received(keeper, ["rack 7"]).status_code == 200
    # One place for a unit.
    assert received(keeper, ["Rack 7", "Rack 8"]).json()["error"]["code"] == "PLACE_ONE_ONLY"
    assert received(keeper, ["Rack 8"]).status_code == 200
    stock = owner.get(f"/api/c/{cmp}/warehouse/stock").json()["stock"]
    assert sorted(stock[0]["tags"]) == ["Rack 7", "Rack 8"]
    # The stock is searched by the place.
    assert len(owner.get(f"/api/c/{cmp}/warehouse/stock?q=rack 8").json()["stock"]) == 1
    assert owner.get(f"/api/c/{cmp}/warehouse/stock?q=rack 9").json()["stock"] == []

    # Renamed in the settings, the place is renamed in the stock at once.
    url = f"{places}/{place['id']}"
    rename = {"name": "Rack 7A", "expected_version": place["version"]}
    done = owner.patch(url, data=rename, content_type="application/json", headers=key())
    assert done.status_code == 200, done.content
    stock = owner.get(f"/api/c/{cmp}/warehouse/stock").json()["stock"]
    assert sorted(stock[0]["tags"]) == ["Rack 7A", "Rack 8"]
    # An archived place is not chosen any more; what lies there keeps it.
    archive = {"archived": True, "expected_version": done.json()["place"]["version"]}
    assert (
        owner.patch(url, data=archive, content_type="application/json", headers=key()).status_code
        == 200
    )
    assert received(keeper, ["Rack 7A"]).json()["error"]["code"] == "PLACE_TAG_UNKNOWN"
    assert Asset.objects.filter(place_id=place["id"]).count() == 1
    # The places are not tags any more: the tags have no group of them.
    assert owner.get(f"/api/c/{cmp}/tags").json()["groups"] == []
