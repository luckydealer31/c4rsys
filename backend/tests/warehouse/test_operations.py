"""Warehouse, part С3: issue and consumption on a vehicle, return, moves with child lots and
shortages, write-off with reasons, repacking, opened packages, expired lots, correction of a
posted receipt (ТЗ § 11.1.17, § 11.2.5, § 11.2.15, § 11.3–11.6; decisions of 25.10.2026)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest import mock

import pytest
from django.test import Client
from django.utils import timezone

from c4rsys.companies.models import ActivityContext, Company, Location
from c4rsys.warehouse.models import Asset, LayerUse, Lot, LotStock, OpenedPackage
from c4rsys.warehouse.stock import expired
from tests.accounts.helpers import post
from tests.companies.helpers import create_company, key

pytestmark = pytest.mark.django_db


@pytest.fixture
def cmp(owner: Client) -> str:
    public_id: str = create_company(owner, timezone="Asia/Bangkok", main_currency="THB").json()[
        "company"
    ]["public_id"]
    return public_id


def location(owner: Client, cmp: str, name: str) -> dict[str, Any]:
    body = {"name": name, "contexts": ["warehouse", "rental"]}
    created: dict[str, Any] = post(owner, f"/api/c/{cmp}/locations", body, key()).json()["location"]
    return created


def warehouse_of(created: dict[str, Any]) -> str:
    return next(str(c["id"]) for c in created["contexts"] if c["type"] == "warehouse")


def item(owner: Client, cmp: str, name: str, unit: str) -> dict[str, Any]:
    created: dict[str, Any] = post(
        owner, f"/api/c/{cmp}/warehouse/items", {"name": name, "unit": unit}, key()
    ).json()["item"]
    return created


def packaging(owner: Client, cmp: str, item_id: str, size: str) -> str:
    url = f"/api/c/{cmp}/warehouse/items/{item_id}/packagings"
    packagings = post(owner, url, {"quantity": size}, key()).json()["item"]["packagings"]
    return str(next(p["id"] for p in packagings if p["quantity"] == f"{Decimal(size):.2f}"))


def vehicle(owner: Client, cmp: str, location_id: str) -> str:
    body = {"name": "Toyota Yaris", "location_id": location_id, "odometer_unit": "km"}
    response = post(owner, f"/api/c/{cmp}/vehicles", body, key())
    assert response.status_code == 201, response.content
    return str(response.json()["vehicle"]["id"])


def receive_stock(owner: Client, cmp: str, ctx: str, lines: list[dict[str, Any]]) -> dict[str, Any]:
    draft = post(
        owner,
        f"/api/c/{cmp}/warehouse/receipts",
        {"source": "initial", "context_id": ctx, "lines": lines},
        key(),
    ).json()["receipt"]
    url = f"/api/c/{cmp}/warehouse/receipts/{draft['id']}/post"
    posted: dict[str, Any] = post(owner, url, {"expected_version": draft["version"]}, key()).json()[
        "receipt"
    ]
    return posted


def op(owner: Client, cmp: str, path: str, body: dict[str, Any]) -> Any:
    return post(owner, f"/api/c/{cmp}/warehouse/operations/{path}", body, key())


@pytest.fixture
def setup(owner: Client, cmp: str) -> dict[str, Any]:
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


def test_issue_return_and_consume_on_a_vehicle(
    owner: Client, cmp: str, setup: dict[str, Any]
) -> None:
    assets = [str(a) for a in Asset.objects.values_list("id", flat=True)]
    issued = op(
        owner,
        cmp,
        "issue",
        {
            "context_id": setup["ctx"],
            "vehicle_id": setup["car"],
            "lines": [{"item_id": setup["pads"], "asset_ids": assets}],
        },
    )
    assert issued.status_code == 201, issued.content
    body = issued.json()["operation"]
    assert body["vehicle"]["name"] == "Toyota Yaris"
    assert set(Asset.objects.values_list("state", flat=True)) == {"issued"}

    lines = body["lines"]
    back = post(
        owner,
        f"/api/c/{cmp}/warehouse/operations/{body['id']}/return",
        {"lines": [{"line_id": lines[0]["id"], "quantity": 1}]},
        key(),
    )
    assert back.status_code == 200, back.content
    used = post(
        owner,
        f"/api/c/{cmp}/warehouse/operations/{body['id']}/consume",
        {"lines": [{"line_id": lines[1]["id"], "quantity": 1}]},
        key(),
    )
    assert used.json()["operation"]["lines"][1]["consumed_quantity"] == "1.00"
    assert sorted(Asset.objects.values_list("state", flat=True)) == ["consumed", "in_stock"]
    again = post(
        owner,
        f"/api/c/{cmp}/warehouse/operations/{body['id']}/return",
        {"lines": [{"line_id": lines[0]["id"], "quantity": 1}]},
        key(),
    )
    assert again.json()["error"]["code"] == "QUANTITY_EXCEEDS_ISSUED"


def test_partial_use_opens_a_package(owner: Client, cmp: str, setup: dict[str, Any]) -> None:
    used = op(
        owner,
        cmp,
        "consume",
        {
            "context_id": setup["ctx"],
            "vehicle_id": setup["car"],
            "lines": [
                {
                    "item_id": setup["oil"],
                    "lot_id": setup["lot"],
                    "packaging_id": setup["can"],
                    "quantity": "5.5",
                }
            ],
        },
    )
    assert used.status_code == 201, used.content
    # One whole canister and 1.5 l of a second one, opened with 2.5 l left (§ 11.1.17).
    opened = OpenedPackage.objects.get()
    assert opened.remaining == Decimal("2.50")
    assert LotStock.objects.get(packaging_id=setup["can"]).quantity == Decimal("4.00")
    assert used.json()["operation"]["lines"][0]["cost"] == "550.00"

    from_opened = op(
        owner,
        cmp,
        "consume",
        {
            "context_id": setup["ctx"],
            "vehicle_id": setup["car"],
            "lines": [
                {
                    "item_id": setup["oil"],
                    "lot_id": setup["lot"],
                    "opened_package_id": str(opened.id),
                    "quantity": "3",
                }
            ],
        },
    )
    assert from_opened.json()["error"]["code"] == "STOCK_NOT_ENOUGH"
    stock = owner.get(f"/api/c/{cmp}/warehouse/stock").json()["stock"]
    oil = next(row for row in stock if row["item"]["id"] == setup["oil"])
    assert oil["quantity"] == "6.50"


def test_vehicle_must_be_of_the_company(owner: Client, cmp: str, setup: dict[str, Any]) -> None:
    refused = op(
        owner,
        cmp,
        "consume",
        {
            "context_id": setup["ctx"],
            "vehicle_id": setup["ctx"],
            "lines": [{"item_id": setup["pads"], "asset_ids": []}],
        },
    )
    assert refused.json()["error"]["field"] in ("vehicle_id", "lines[0].asset_ids")


def test_expired_lot_warning_and_ban(owner: Client, cmp: str, setup: dict[str, Any]) -> None:
    Lot.objects.update(expiry_date=timezone.localdate() - timedelta(days=1))
    line = {
        "item_id": setup["oil"],
        "lot_id": setup["lot"],
        "packaging_id": setup["can"],
        "quantity": "4",
    }
    body = {"context_id": setup["ctx"], "vehicle_id": setup["car"], "lines": [line]}
    assert op(owner, cmp, "consume", body).json()["error"]["code"] == "EXPIRED_LOT_CONFIRM"
    confirmed = op(owner, cmp, "consume", {**body, "lines": [{**line, "confirm_expired": True}]})
    assert confirmed.status_code == 201
    assert confirmed.json()["operation"]["lines"][0]["expired_confirmed"] is True

    url = f"/api/c/{cmp}/warehouse/settings"
    changed = owner.put(
        url,
        data={"expired_policy": "forbid", "expected_version": 1},
        content_type="application/json",
        headers=key(),
    )
    assert changed.status_code == 200, changed.content
    forbidden = op(owner, cmp, "consume", {**body, "lines": [{**line, "confirm_expired": True}]})
    assert forbidden.json()["error"]["code"] == "EXPIRED_LOT_FORBIDDEN"


def test_move_child_lot_shortage_and_settling(
    owner: Client, cmp: str, setup: dict[str, Any]
) -> None:
    krabi = warehouse_of(location(owner, cmp, "Krabi"))
    pad = str(Asset.objects.first().id)  # type: ignore[union-attr]
    sent = op(
        owner,
        cmp,
        "move",
        {
            "context_id": setup["ctx"],
            "target_context_id": krabi,
            "lines": [
                {
                    "item_id": setup["oil"],
                    "lot_id": setup["lot"],
                    "packaging_id": setup["can"],
                    "quantity": "8",
                },
                {"item_id": setup["pads"], "asset_ids": [pad]},
            ],
        },
    ).json()["operation"]
    assert sent["state"] == "in_transit"
    assert Asset.objects.get(pk=pad).state == "in_transit"

    oil_line, pad_line = sent["lines"]
    received = post(
        owner,
        f"/api/c/{cmp}/warehouse/operations/{sent['id']}/receive",
        {"lines": [{"line_id": oil_line["id"], "quantity": "4"}]},
        key(),
    ).json()["operation"]
    assert received["state"] == "discrepancy"
    child = Lot.objects.get(parent__isnull=False)
    assert str(child.context_id) == krabi and child.layers.get().unit_cost == Decimal("100")
    assert received["lines"][0]["shortage"] == "4.00"

    url = f"/api/c/{cmp}/warehouse/operations/{sent['id']}/settle"
    back = post(owner, url, {"line_id": oil_line["id"], "action": "return"}, key())
    assert back.status_code == 200, back.content
    other = next(
        r
        for r in owner.get(f"/api/c/{cmp}/warehouse/reasons").json()["reasons"]
        if r["code"] == "loss"
    )
    lost = post(
        owner,
        url,
        {"line_id": pad_line["id"], "action": "write_off", "reason_id": other["id"]},
        key(),
    )
    assert lost.json()["operation"]["state"] == "received"
    assert Asset.objects.get(pk=pad).state == "written_off"
    # 12 l: 4 l moved to Krabi; the 4 l returned are a whole canister again, 8 l at home.
    home = LotStock.objects.filter(lot_id=setup["lot"])
    assert home.get(packaging_id=setup["can"]).quantity == Decimal("8.00")
    assert not home.filter(packaging__isnull=True, quantity__gt=0).exists()


def test_one_step_move(owner: Client, cmp: str, setup: dict[str, Any]) -> None:
    krabi = warehouse_of(location(owner, cmp, "Krabi"))
    moved = op(
        owner,
        cmp,
        "move",
        {
            "context_id": setup["ctx"],
            "target_context_id": krabi,
            "one_step": True,
            "lines": [
                {
                    "item_id": setup["oil"],
                    "lot_id": setup["lot"],
                    "packaging_id": setup["can"],
                    "quantity": "4",
                }
            ],
        },
    )
    assert moved.json()["operation"]["state"] == "received"
    same = op(
        owner,
        cmp,
        "move",
        {"context_id": setup["ctx"], "target_context_id": setup["ctx"], "lines": []},
    )
    assert same.json()["error"]["code"] in ("MOVE_SAME_WAREHOUSE", "LINES_REQUIRED")


def test_write_off_reasons_and_no_negative_stock(
    owner: Client, cmp: str, setup: dict[str, Any]
) -> None:
    reasons = owner.get(f"/api/c/{cmp}/warehouse/reasons").json()["reasons"]
    assert [r["code"] for r in reasons] == ["damage", "expiry", "loss", "handling_loss", "other"]
    other = next(r for r in reasons if r["code"] == "other")
    line = {
        "item_id": setup["oil"],
        "lot_id": setup["lot"],
        "packaging_id": setup["can"],
        "quantity": "1",
    }
    body = {"context_id": setup["ctx"], "reason_id": other["id"], "lines": [line]}
    assert op(owner, cmp, "write-off", body).json()["error"]["code"] == "COMMENT_REQUIRED_FOR_OTHER"
    done = op(owner, cmp, "write-off", {**body, "comment": "Spilled"})
    assert done.status_code == 201, done.content
    too_much = op(
        owner, cmp, "write-off", {**body, "comment": "x", "lines": [{**line, "quantity": "40"}]}
    )
    assert too_much.json()["error"]["code"] == "STOCK_NOT_ENOUGH"

    renamed = owner.patch(
        f"/api/c/{cmp}/warehouse/reasons/{other['id']}",
        data={"name": "Другое"},
        content_type="application/json",
        headers=key(),
    )
    assert renamed.json()["reason"]["name"] == "Другое"


def test_repack_must_balance(owner: Client, cmp: str, setup: dict[str, Any]) -> None:
    bottle = packaging(owner, cmp, setup["oil"], "1")
    reasons = owner.get(f"/api/c/{cmp}/warehouse/reasons").json()["reasons"]
    handling = next(r for r in reasons if r["code"] == "handling_loss")
    body = {
        "context_id": setup["ctx"],
        "lot_id": setup["lot"],
        "source": {"packaging_id": setup["can"], "quantity": "4"},
        "outputs": [{"packaging_id": bottle, "quantity": "3"}],
    }
    assert op(owner, cmp, "repack", body).json()["error"]["code"] == "REPACK_NOT_BALANCED"
    done = op(
        owner,
        cmp,
        "repack",
        {
            **body,
            "loss": "0.5",
            "loss_reason_id": handling["id"],
            "outputs": [
                {"packaging_id": bottle, "quantity": "3"},
                {"packaging_id": None, "quantity": "0.5"},
            ],
        },
    )
    assert done.status_code == 201, done.content
    assert done.json()["operation"]["children"][0]["kind"] == "write_off"
    lot = Lot.objects.get(pk=setup["lot"])
    assert sum(layer.remaining for layer in lot.layers.all()) == Decimal("11.50")
    assert LotStock.objects.get(lot=lot, packaging_id=bottle).quantity == Decimal("3.00")


def test_correction_of_a_posted_receipt(owner: Client, cmp: str, setup: dict[str, Any]) -> None:
    receipt = owner.get(f"/api/c/{cmp}/warehouse/receipts").json()["receipts"][0]
    oil_line = next(line for line in receipt["lines"] if line["item"]["id"] == setup["oil"])
    url = f"/api/c/{cmp}/warehouse/receipts/{receipt['id']}/lines/{oil_line['id']}/correct"
    assert post(owner, url, {"quantity": "8"}, key()).json()["error"]["field"] == "reason"
    smaller = post(owner, url, {"quantity": "8", "reason": "Counted wrong"}, key())
    assert smaller.status_code == 200, smaller.content
    op(
        owner,
        cmp,
        "consume",
        {
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
        },
    )
    blocked = post(owner, url, {"quantity": "4", "reason": "Again"}, key())
    assert blocked.json()["error"]["code"] == "CORRECTION_BLOCKED"
    # § 11.2.5: a changed cost reaches what was already used.
    priced = post(owner, url, {"unit_cost": "120", "reason": "Invoice"}, key())
    assert priced.status_code == 200
    assert {use.unit_cost for use in LayerUse.objects.all()} == {Decimal("120")}


@pytest.mark.parametrize(
    ("location_zone", "company_zone", "is_expired"),
    [("Asia/Bangkok", "", True), ("", "Asia/Bangkok", True), ("", "", False)],
)
def test_expiry_is_judged_by_the_date_at_the_warehouse(
    location_zone: str, company_zone: str, is_expired: bool
) -> None:
    """At 17:30 UTC on 25 October it is already the 26th in Bangkok (§ 2.7.5, § 11.2.15):
    a lot expiring on the 25th is expired there. Without the zone of the location the zone of
    the company is used (§ 3.1.6)."""
    company = Company(timezone=company_zone)
    context = ActivityContext(location=Location(company=company, timezone=location_zone))
    lot = Lot(context=context, expiry_date=date(2026, 10, 25))
    evening = datetime(2026, 10, 25, 17, 30, tzinfo=UTC)
    with mock.patch("django.utils.timezone.now", return_value=evening):
        assert expired(lot) is is_expired
