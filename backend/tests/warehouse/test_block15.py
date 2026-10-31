"""Warehouse, block 15: separate reserves with a term (§ 11.7.1, § 11.8.1), the mode of reserves
for works (§ 11.7.7), correction of posted issues, write-offs and moves (§ 11.6.2), return of an
issue into another warehouse (§ 11.4.5), the low-stock notification counted with reserves
(§ 11.7.8) and the selling price in a receipt (§ 11.2.7, § 11.7.6, § 23.7.4)."""

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from django.test import Client
from django.utils import timezone

from c4rsys.notifications.models import Notification
from c4rsys.platform.models import AuditEvent
from c4rsys.warehouse import notices, reserves
from c4rsys.warehouse.models import (
    Asset,
    Item,
    Lot,
    LotStock,
    OperationCorrection,
    SalePrice,
    StockReserve,
)
from tests.accounts.helpers import post
from tests.companies.helpers import create_company, hire, key
from tests.warehouse.test_operations import (
    item,
    location,
    op,
    packaging,
    receive_stock,
    vehicle,
    warehouse_of,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def cmp(owner: Client) -> str:
    created = create_company(owner, timezone="Asia/Bangkok", main_currency="THB")
    public_id: str = created.json()["company"]["public_id"]
    return public_id


@pytest.fixture
def setup(owner: Client, cmp: str) -> dict[str, Any]:
    """Phuket: oil in three 4 l cans at 100, two pads at 500, a car of the company."""
    phuket = location(owner, cmp, "Phuket")
    ctx = warehouse_of(phuket)
    oil = item(owner, cmp, "Oil", "liter")
    can = packaging(owner, cmp, oil["id"], "4")
    pads = item(owner, cmp, "Pads", "piece")
    receive_stock(
        owner,
        cmp,
        ctx,
        [
            {"item_id": oil["id"], "packaging_id": can, "packs": 3, "unit_cost": "100"},
            {"item_id": pads["id"], "quantity": 2, "unit_cost": "500"},
        ],
    )
    return {
        "ctx": ctx,
        "location": phuket["id"],
        "oil": oil["id"],
        "can": can,
        "pads": pads["id"],
        "lot": str(Lot.objects.get().id),
        "car": vehicle(owner, cmp, phuket["id"]),
    }


def _code(response: Any) -> str:
    code: str = response.json()["error"]["code"]
    return code


def _reserve(owner: Client, cmp: str, body: dict[str, Any]) -> Any:
    return post(owner, f"/api/c/{cmp}/warehouse/reserves", body, key())


def _pads(setup: dict[str, Any]) -> list[str]:
    return [
        str(a)
        for a in Asset.objects.filter(item_id=setup["pads"])
        .order_by("created_at", "id")
        .values_list("id", flat=True)
    ]


def _cans(setup: dict[str, Any]) -> Decimal:
    row = LotStock.objects.get(lot_id=setup["lot"], packaging_id=setup["can"])
    return row.quantity


def _put(owner: Client, url: str, body: dict[str, Any]) -> Any:
    return owner.put(url, data=body, content_type="application/json", headers=key())


# --- Reserves (§ 11.7.1) ------------------------------------------------------------------------


def test_a_reserve_holds_a_unit_until_its_issue_uses_it(
    owner: Client, cmp: str, setup: dict[str, Any]
) -> None:
    pad = _pads(setup)[0]
    body = {"context_id": setup["ctx"], "item_id": setup["pads"], "asset_id": pad}
    # A separate reserve says what it is for (PROPOSAL).
    assert _code(_reserve(owner, cmp, body)) == "FIELD_REQUIRED"
    made = _reserve(owner, cmp, {**body, "comment": "Brakes of the Yaris on Monday"})
    assert made.status_code == 201, made.content
    reserve = made.json()["reserve"]
    assert reserve["state"] == "active" and reserve["number"] == 1
    # Reserved stays in the warehouse but is not available to other operations (§ 11.7.1).
    assert Asset.objects.get(pk=pad).state == "in_stock"
    assert _code(_reserve(owner, cmp, {**body, "comment": "Again"})) == "ASSET_NOT_AVAILABLE"
    lines = [{"item_id": setup["pads"], "asset_ids": [pad]}]
    issue = {"context_id": setup["ctx"], "vehicle_id": setup["car"], "lines": lines}
    assert _code(op(owner, cmp, "issue", issue)) == "ASSET_NOT_AVAILABLE"

    # The issue for its purpose takes what is reserved under it (§ 11.8.1).
    lines[0]["reserve_id"] = reserve["id"]
    issued = op(owner, cmp, "issue", issue)
    assert issued.status_code == 201, issued.content
    stored = StockReserve.objects.get(pk=reserve["id"])
    assert (stored.state, stored.used_quantity) == ("used", Decimal(1))
    assert AuditEvent.objects.filter(action="warehouse.reserve_used").exists()


def test_a_reserve_of_closed_packages_is_whole_and_never_beyond_the_free(
    owner: Client, cmp: str, setup: dict[str, Any]
) -> None:
    body = {
        "context_id": setup["ctx"],
        "item_id": setup["oil"],
        "lot_id": setup["lot"],
        "packaging_id": setup["can"],
        "quantity": "5",
        "comment": "Oil change",
    }
    made = _reserve(owner, cmp, body)
    assert made.status_code == 201, made.content
    # 5 l of closed 4 l cans hold two cans whole (PROPOSAL).
    assert made.json()["reserve"]["quantity"] == "8.00"
    # 12 l in stock, 8 reserved: a new reserve or a write-off gets only the free 4 l.
    refused = _reserve(owner, cmp, {**body, "quantity": "8"})
    assert _code(refused) == "STOCK_NOT_ENOUGH"
    assert refused.json()["error"]["params"]["available"] == "4.00"
    issue = {
        "context_id": setup["ctx"],
        "vehicle_id": setup["car"],
        "lines": [
            {
                "item_id": setup["oil"],
                "lot_id": setup["lot"],
                "packaging_id": setup["can"],
                "quantity": "8",
            }
        ],
    }
    assert _code(op(owner, cmp, "issue", issue)) == "STOCK_NOT_ENOUGH"
    issue["lines"][0]["quantity"] = "4"
    assert op(owner, cmp, "issue", issue).status_code == 201
    assert _cans(setup) == Decimal(8)


def test_a_reserve_with_a_term_is_released_when_it_ends(
    owner: Client, cmp: str, setup: dict[str, Any]
) -> None:
    pad = _pads(setup)[0]
    body = {
        "context_id": setup["ctx"],
        "item_id": setup["pads"],
        "asset_id": pad,
        "comment": "Hold for the client",
        "expires_at": (timezone.now() - timedelta(minutes=1)).isoformat(),
    }
    assert _code(_reserve(owner, cmp, body)) == "RESERVE_TERM_PAST"
    body["expires_at"] = (timezone.now() + timedelta(hours=2)).isoformat()
    reserve = _reserve(owner, cmp, body).json()["reserve"]
    assert reserves.asset_reserved(Asset.objects.get(pk=pad).id)

    # Past the term the unused reserve holds nothing at once and is marked by the check.
    StockReserve.objects.filter(pk=reserve["id"]).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert not reserves.asset_reserved(Asset.objects.get(pk=pad).id)
    assert reserves.expire(timezone.now()) == 1
    assert StockReserve.objects.get(pk=reserve["id"]).state == "expired"
    entry = AuditEvent.objects.get(action="warehouse.reserve_expired")
    assert entry.system_process == "warehouse_reserve_expiry"


def test_a_released_reserve_frees_the_stock(owner: Client, cmp: str, setup: dict[str, Any]) -> None:
    pad = _pads(setup)[0]
    reserve = _reserve(
        owner,
        cmp,
        {"context_id": setup["ctx"], "item_id": setup["pads"], "asset_id": pad, "comment": "x"},
    ).json()["reserve"]
    released = post(
        owner,
        f"/api/c/{cmp}/warehouse/reserves/{reserve['id']}/release",
        {"reason": "The client refused"},
        key(),
    )
    assert released.status_code == 200, released.content
    assert released.json()["reserve"]["state"] == "released"
    listed = owner.get(f"/api/c/{cmp}/warehouse/reserves?state=released").json()["reserves"]
    assert [r["id"] for r in listed] == [reserve["id"]]
    issue = {
        "context_id": setup["ctx"],
        "vehicle_id": setup["car"],
        "lines": [{"item_id": setup["pads"], "asset_ids": [pad]}],
    }
    assert op(owner, cmp, "issue", issue).status_code == 201


def test_reserves_need_the_reserve_right(owner: Client, cmp: str, setup: dict[str, Any]) -> None:
    rights = [
        {
            "type": "grant",
            "permission": "warehouse.view",
            "locations": "all",
            "contexts": "all",
            "cash_accounts": "all",
        }
    ]
    viewer, _ = hire(owner, cmp, "viewer@example.com", rights)
    body = {
        "context_id": setup["ctx"],
        "item_id": setup["pads"],
        "asset_id": _pads(setup)[0],
        "comment": "x",
    }
    assert _reserve(viewer, cmp, body).status_code == 403


def test_the_mode_of_reserves_for_works(owner: Client, cmp: str, setup: dict[str, Any]) -> None:
    url = f"/api/c/{cmp}/warehouse/settings"
    current = owner.get(url).json()["settings"]
    assert current["work_reserve_mode"] == "auto"  # § 11.7.7: automatic by default
    changed = _put(owner, url, {"work_reserve_mode": "manual", "expected_version": 1})
    assert changed.status_code == 200, changed.content
    assert changed.json()["settings"] == {
        "expired_policy": "warn",
        "work_reserve_mode": "manual",
        "version": 2,
    }
    assert (
        _put(owner, url, {"work_reserve_mode": "sometimes", "expected_version": 2}).status_code
        == 400
    )


# --- Low stock (§ 11.7.8) -----------------------------------------------------------------------


def test_low_stock_counts_reserves_and_notifies(
    owner: Client, cmp: str, setup: dict[str, Any]
) -> None:
    minimum = f"/api/c/{cmp}/warehouse/items/{setup['oil']}/minimum"
    assert _put(owner, minimum, {"context_id": setup["ctx"], "quantity": "10"}).status_code == 200
    now = timezone.now()
    # No threshold breach yet: 12 l available.
    assert notices.low_stock(now) == 0
    reserve = _reserve(
        owner,
        cmp,
        {
            "context_id": setup["ctx"],
            "item_id": setup["oil"],
            "lot_id": setup["lot"],
            "packaging_id": setup["can"],
            "quantity": "4",
            "comment": "Oil change",
        },
    ).json()["reserve"]
    row = owner.get(f"/api/c/{cmp}/warehouse/low-stock").json()["low_stock"][0]
    # 12 l in stock, 4 reserved: 8 available is below 10.
    assert (row["actual"], row["reserved"], row["available"]) == ("12.00", "4.00", "8.00")
    assert notices.low_stock(now) == 1
    notice = Notification.objects.get(event="warehouse.low_stock")
    assert notice.params["item"] == "Oil" and notice.state == "active"
    # Released — back at the threshold, the notice ends.
    post(owner, f"/api/c/{cmp}/warehouse/reserves/{reserve['id']}/release", {}, key())
    notices.low_stock(now)
    notice.refresh_from_db()
    assert notice.state != "active"


# --- Correction of posted operations (§ 11.6.2) ---------------------------------------------------


def _correct(owner: Client, cmp: str, operation: dict[str, Any], line: int, **body: Any) -> Any:
    line_id = operation["lines"][line]["id"]
    url = f"/api/c/{cmp}/warehouse/operations/{operation['id']}/lines/{line_id}/correct"
    return post(owner, url, body, key())


def test_a_posted_issue_is_corrected_down_and_keeps_its_record(
    owner: Client, cmp: str, setup: dict[str, Any]
) -> None:
    loose = {"item_id": setup["oil"], "lot_id": setup["lot"], "packaging_id": setup["can"]}
    issue = {
        "context_id": setup["ctx"],
        "vehicle_id": setup["car"],
        "lines": [{**loose, "quantity": "6"}],
    }
    issued = op(owner, cmp, "issue", issue).json()["operation"]
    assert _cans(setup) == Decimal(4)  # 6 l opened the second can
    assert _correct(owner, cmp, issued, 0, quantity="4").status_code == 400  # a reason
    refused = _correct(owner, cmp, issued, 0, quantity="6", reason="Typo")
    assert _code(refused) == "CORRECTION_MUST_DECREASE"
    corrected = _correct(owner, cmp, issued, 0, quantity="4", reason="Two litres were not taken")
    assert corrected.status_code == 200, corrected.content
    line = corrected.json()["operation"]["lines"][0]
    # The posted quantity stays; the correction, its reason and author are kept.
    assert (line["quantity"], line["cancelled_quantity"], line["outstanding"]) == (
        "6.00",
        "2.00",
        "4.00",
    )
    assert line["corrections"][0]["reason"] == "Two litres were not taken"
    assert OperationCorrection.objects.count() == 1
    # The two litres came back into the opened can of the same lot.
    lot = Lot.objects.get(pk=setup["lot"])
    assert sum(o.remaining for o in lot.opened_packages.all()) == Decimal(4)

    # Returned or consumed parts are not corrected away: they are not outstanding.
    post(
        owner,
        f"/api/c/{cmp}/warehouse/operations/{issued['id']}/return",
        {"lines": [{"line_id": line["id"], "quantity": "3"}]},
        key(),
    )
    blocked = _correct(owner, cmp, issued, 0, quantity="0", reason="All wrong")
    assert _code(blocked) == "CORRECTION_BLOCKED"
    assert blocked.json()["error"]["params"]["available"] == "1.00"
    assert _correct(owner, cmp, issued, 0, quantity="3", reason="One more").status_code == 200


def test_a_write_off_of_a_unit_is_corrected_back_into_stock(
    owner: Client, cmp: str, setup: dict[str, Any]
) -> None:
    reasons = owner.get(f"/api/c/{cmp}/warehouse/reasons").json()["reasons"]
    damage = next(r["id"] for r in reasons if r["code"] == "damage")
    pad = _pads(setup)[0]
    written = op(
        owner,
        cmp,
        "write-off",
        {
            "context_id": setup["ctx"],
            "reason_id": damage,
            "lines": [{"item_id": setup["pads"], "asset_ids": [pad]}],
        },
    ).json()["operation"]
    assert Asset.objects.get(pk=pad).state == "written_off"
    fixed = _correct(owner, cmp, written, 0, quantity="0", reason="Wrong unit written off")
    assert fixed.status_code == 200, fixed.content
    assert Asset.objects.get(pk=pad).state == "in_stock"
    assert fixed.json()["operation"]["lines"][0]["correctable"] == "0.00"
    again = _correct(owner, cmp, written, 0, quantity="0", reason="Again")
    assert _code(again) == "CORRECTION_NOT_SUPPORTED"


def test_a_move_in_transit_is_corrected_back_to_the_sender(
    owner: Client, cmp: str, setup: dict[str, Any]
) -> None:
    other = warehouse_of(location(owner, cmp, "Krabi"))
    sent = op(
        owner,
        cmp,
        "move",
        {
            "context_id": setup["ctx"],
            "target_context_id": other,
            "lines": [
                {
                    "item_id": setup["oil"],
                    "lot_id": setup["lot"],
                    "packaging_id": setup["can"],
                    "quantity": "4",
                }
            ],
        },
    ).json()["operation"]
    assert _cans(setup) == Decimal(8)
    corrected = _correct(owner, cmp, sent, 0, quantity="0", reason="Not sent after all")
    assert corrected.status_code == 200, corrected.content
    assert corrected.json()["operation"]["state"] == "cancelled"
    assert _cans(setup) == Decimal(12)
    # A received move is corrected by a move back (PROPOSAL).
    one_step = op(
        owner,
        cmp,
        "move",
        {
            "context_id": setup["ctx"],
            "target_context_id": other,
            "one_step": True,
            "lines": [{"item_id": setup["pads"], "asset_ids": [_pads(setup)[0]]}],
        },
    ).json()["operation"]
    assert _code(_correct(owner, cmp, one_step, 0, quantity="0", reason="x")) == (
        "CORRECTION_NOT_SUPPORTED"
    )


def test_a_correction_needs_the_correct_right(
    owner: Client, cmp: str, setup: dict[str, Any]
) -> None:
    rights = [
        {
            "type": "grant",
            "permission": permission,
            "locations": "all",
            "contexts": "all",
            "cash_accounts": "all",
        }
        for permission in ("warehouse.view", "warehouse.issue")
    ]
    clerk, _ = hire(owner, cmp, "clerk@example.com", rights)
    issued = op(
        owner,
        cmp,
        "issue",
        {
            "context_id": setup["ctx"],
            "vehicle_id": setup["car"],
            "lines": [{"item_id": setup["pads"], "asset_ids": [_pads(setup)[0]]}],
        },
    ).json()["operation"]
    assert _correct(clerk, cmp, issued, 0, quantity="0", reason="x").status_code == 403


# --- Return into another warehouse (§ 11.4.5) -----------------------------------------------------


def test_an_issue_returns_into_another_warehouse_through_a_move(
    owner: Client, cmp: str, setup: dict[str, Any]
) -> None:
    other = warehouse_of(location(owner, cmp, "Krabi"))
    pad = _pads(setup)[0]
    issued = op(
        owner,
        cmp,
        "issue",
        {
            "context_id": setup["ctx"],
            "vehicle_id": setup["car"],
            "lines": [
                {"item_id": setup["pads"], "asset_ids": [pad]},
                {
                    "item_id": setup["oil"],
                    "lot_id": setup["lot"],
                    "packaging_id": setup["can"],
                    "quantity": "4",
                },
            ],
        },
    ).json()["operation"]
    back = post(
        owner,
        f"/api/c/{cmp}/warehouse/operations/{issued['id']}/return",
        {
            "target_context_id": other,
            "lines": [
                {"line_id": issued["lines"][0]["id"], "quantity": 1},
                {"line_id": issued["lines"][1]["id"], "quantity": "4"},
            ],
        },
        key(),
    )
    assert back.status_code == 200, back.content
    # The same unit with its ID is now in the other warehouse; the material is a child lot.
    assert str(Asset.objects.get(pk=pad).context_id) == other
    child = Lot.objects.get(context_id=UUID(other))
    assert str(child.parent_id) == setup["lot"]
    moves = owner.get(f"/api/c/{cmp}/warehouse/operations?kind=move").json()["operations"]
    assert len(moves) == 1 and moves[0]["state"] == "received"
    assert moves[0]["parent_id"] == issued["id"]


# --- Selling price in a receipt (§ 11.2.7, § 11.7.6, § 23.7.4) ------------------------------------


def _draft(client: Client, cmp: str, ctx: str, lines: list[dict[str, Any]]) -> Any:
    return post(
        client,
        f"/api/c/{cmp}/warehouse/receipts",
        {"source": "purchase", "context_id": ctx, "lines": lines},
        key(),
    )


def test_a_receipt_sets_the_selling_price_at_posting(
    owner: Client, cmp: str, setup: dict[str, Any]
) -> None:
    wheel = item(owner, cmp, "Wheel", "piece")
    draft = _draft(
        owner,
        cmp,
        setup["ctx"],
        [
            {"item_id": wheel["id"], "quantity": 2, "unit_cost": "900", "sale_price": "1500"},
            {
                "item_id": setup["oil"],
                "packaging_id": setup["can"],
                "packs": 1,
                "unit_cost": "100",
                "sale_price": "650",
            },
        ],
    )
    assert draft.status_code == 201, draft.content
    receipt = draft.json()["receipt"]
    assert [line["sale_price"] for line in receipt["lines"]] == ["1500.00", "650.00"]
    assert not SalePrice.objects.filter(item_id=wheel["id"]).exists()  # a draft sets nothing
    posted = post(
        owner,
        f"/api/c/{cmp}/warehouse/receipts/{receipt['id']}/post",
        {"expected_version": receipt["version"]},
        key(),
    )
    assert posted.status_code == 200, posted.content
    # A piece — the price per piece; a material in the item mode — the price of its packaging.
    assert SalePrice.objects.get(item_id=wheel["id"]).amount == Decimal(1500)
    oil = SalePrice.objects.get(item_id=setup["oil"])
    assert (oil.amount, str(oil.packaging_id), oil.lot_id) == (Decimal(650), setup["can"], None)


def test_a_receipt_price_in_the_lot_mode_goes_to_the_lot(
    owner: Client, cmp: str, setup: dict[str, Any]
) -> None:
    Item.objects.filter(pk=setup["oil"]).update(price_mode="lot")
    receipt = _draft(
        owner,
        cmp,
        setup["ctx"],
        [{"item_id": setup["oil"], "quantity": "5", "unit_cost": "30", "sale_price": "45"}],
    ).json()["receipt"]
    post(
        owner,
        f"/api/c/{cmp}/warehouse/receipts/{receipt['id']}/post",
        {"expected_version": receipt["version"]},
        key(),
    )
    new_lot = Lot.objects.exclude(pk=setup["lot"]).get(item_id=setup["oil"])
    price = SalePrice.objects.get(item_id=setup["oil"], lot=new_lot)
    assert (price.amount, price.packaging_id) == (Decimal(45), None)


def test_a_receipt_price_needs_the_price_right(
    owner: Client, cmp: str, setup: dict[str, Any]
) -> None:
    rights = [
        {
            "type": "grant",
            "permission": permission,
            "locations": "all",
            "contexts": "all",
            "cash_accounts": "all",
        }
        for permission in ("warehouse.view", "warehouse.receive")
    ]
    clerk, _ = hire(owner, cmp, "clerk@example.com", rights)
    line = {"item_id": setup["pads"], "quantity": 1, "unit_cost": "500"}
    assert _draft(clerk, cmp, setup["ctx"], [{**line, "sale_price": "800"}]).status_code == 403
    # Without a price the goods come in; the earlier price stays.
    assert _draft(clerk, cmp, setup["ctx"], [line]).status_code == 201
