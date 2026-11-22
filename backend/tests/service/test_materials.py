"""Service С3: parts from a warehouse to a work, return unused, consumption and charge
(ТЗ § 16.5.1–16.5.2, § 16.7.3)."""

from decimal import Decimal
from typing import Any

import pytest
from django.test import Client

from c4rsys.warehouse.models import Asset, LotStock
from tests.accounts.helpers import post
from tests.companies.helpers import hire, key
from tests.rentals.scenario import Scenario
from tests.service.test_service import (
    _at,
    _code,
    _do,
    _job,
    _ok,
    _op,
    _order,
    _work,
)
from tests.warehouse.test_operations import item, receive_stock

pytestmark = pytest.mark.django_db


@pytest.fixture
def stock(owner: Client, s: Scenario) -> dict[str, Any]:
    created = post(
        owner, f"/api/c/{s.cmp}/locations/{s.location}/contexts", {"type": "warehouse"}, key()
    )
    assert created.status_code == 201, created.content
    contexts = owner.get(f"/api/c/{s.cmp}/locations").json()["locations"][0]["contexts"]
    ctx = next(c["id"] for c in contexts if c["type"] == "warehouse")
    oil = item(owner, s.cmp, "Oil 10W-40", "liter")
    filters = item(owner, s.cmp, "Oil filter", "piece")
    receive_stock(
        owner,
        s.cmp,
        ctx,
        [
            {"item_id": oil["id"], "quantity": "10", "unit_cost": "150"},
            {"item_id": filters["id"], "quantity": "3", "unit_cost": "200"},
        ],
    )
    owner.put(
        f"/api/c/{s.cmp}/warehouse/items/{oil['id']}/price",
        data={"amount": "250"},
        content_type="application/json",
        headers=key(),
    )
    return {"ctx": ctx, "oil": oil["id"], "filter": filters["id"]}


def _lot(item_id: str) -> str:
    return str(LotStock.objects.filter(lot__item_id=item_id, quantity__gt=0).first().lot_id)  # type: ignore[union-attr]


def _asset(item_id: str) -> str:
    return str(Asset.objects.filter(item_id=item_id).first().id)  # type: ignore[union-attr]


def _add(
    client: Client, s: Scenario, job: dict[str, Any], stock: dict[str, Any], **body: Any
) -> Any:
    data = {"expected_version": job["version"], "context_id": stock["ctx"], **body}
    return post(client, f"/api/c/{s.cmp}/service/jobs/{job['id']}/materials", data, key())


def _material(client: Client, s: Scenario, m: dict[str, Any], action: str, **body: Any) -> Any:
    data = {"expected_version": m["version"], **body}
    return post(client, f"/api/c/{s.cmp}/service/materials/{m['id']}/{action}", data, key())


def _left(item_id: str) -> Decimal:
    return sum((r.quantity for r in LotStock.objects.filter(lot__item_id=item_id)), Decimal(0))


def test_done_work_consumes_and_charges(owner: Client, s: Scenario, stock: dict[str, Any]) -> None:
    oil_change = _work(owner, s, price="300")
    op = _op(owner, s, [oil_change["id"]])
    op = _ok(_do(owner, s, op, "accept", accepted_at=_at(-3)))
    added = _add(
        owner,
        s,
        op["jobs"][0],
        stock,
        item_id=stock["oil"],
        lot_id=_lot(stock["oil"]),
        quantity="4",
    )
    assert added.status_code == 201, added.content
    op = added.json()["operation"]
    assert _left(stock["oil"]) == Decimal(6)
    material = op["materials"][0]
    assert material["outstanding"] == "4" and material["price"] == "250"

    # One litre is not needed and goes back unused (§ 16.5.1).
    op = _ok(_material(owner, s, material, "return", quantity="1"))
    assert _left(stock["oil"]) == Decimal(7)
    assert _order(owner, s, op["order_id"])["amounts"] == {}

    op = _ok(_job(owner, s, op["jobs"][0], "start", actual_start=_at(-2)))
    op = _ok(_job(owner, s, op["jobs"][0], "complete", actual_end=_at(-1), odometer="10500"))
    material = op["materials"][0]
    assert material["consumed"] == "3" and material["outstanding"] == "0"
    # Work 300 + 3 l × 250 (§ 16.1.3: work and materials apart).
    assert _order(owner, s, op["order_id"])["amounts"]["THB"]["charged"] == "1050.00"


def test_unsettled_and_unpriced_material_block_closing(
    owner: Client, s: Scenario, stock: dict[str, Any]
) -> None:
    oil_change = _work(owner, s, price="300")
    op = _op(owner, s, [oil_change["id"]])
    op = _ok(_do(owner, s, op, "accept", accepted_at=_at(-3)))
    op = _add(
        owner, s, op["jobs"][0], stock, item_id=stock["filter"], asset_ids=[_asset(stock["filter"])]
    ).json()
    assert "operation" in op, op
    op = op["operation"]
    # A part still issued keeps the work from being cancelled without returning it.
    assert _code(_job(owner, s, op["jobs"][0], "cancel", reason="x")) == "SERVICE_MATERIALS_ISSUED"

    op = _ok(_job(owner, s, op["jobs"][0], "start", actual_start=_at(-2)))
    op = _ok(
        _job(owner, s, op["jobs"][0], "terminate", actual_end=_at(-1), reason="Stop", price="0")
    )
    op = _ok(_do(owner, s, op, "finish"))
    order = _order(owner, s, op["order_id"])
    assert [c["condition"] for c in order["closing_conditions"]] == ["service_materials_unsettled"]

    material = op["materials"][0]
    op = _ok(_material(owner, s, material, "consume"))
    order = _order(owner, s, op["order_id"])
    # The filter has no selling price yet (§ 16.1.4).
    assert [c["condition"] for c in order["closing_conditions"]] == ["service_price_missing"]
    op = _ok(_material(owner, s, op["materials"][0], "price", price="450"))
    order = _order(owner, s, op["order_id"])
    assert order["closing_conditions"] == []
    assert order["amounts"]["THB"]["charged"] == "450.00"


def test_mechanic_cannot_issue_but_consumes_with_done(
    owner: Client, s: Scenario, stock: dict[str, Any]
) -> None:
    oil_change = _work(owner, s, price="300")
    mechanic, _ = hire(owner, s.cmp, "mech@example.com", [{"type": "set", "set": "mechanic"}])
    op = _op(owner, s, [oil_change["id"]])
    op = _ok(_do(owner, s, op, "accept", accepted_at=_at(-3)))
    refused = _add(
        mechanic,
        s,
        op["jobs"][0],
        stock,
        item_id=stock["oil"],
        lot_id=_lot(stock["oil"]),
        quantity="1",
    )
    assert refused.status_code == 403
    op = _add(
        owner,
        s,
        op["jobs"][0],
        stock,
        item_id=stock["oil"],
        lot_id=_lot(stock["oil"]),
        quantity="1",
    ).json()["operation"]
    op = _ok(_job(mechanic, s, op["jobs"][0], "take"))
    op = _ok(_job(mechanic, s, op["jobs"][0], "start", actual_start=_at(-2)))
    op = _ok(_job(mechanic, s, op["jobs"][0], "complete", actual_end=_at(-1), odometer="10500"))
    assert op["materials"][0]["consumed"] == "1"


def test_goods_of_the_order_are_linked_to_a_work_and_unlinked(
    owner: Client, s: Scenario, stock: dict[str, Any]
) -> None:
    """Customer's decision of 22.11.2026: goods added to the order of a service are linked to a
    work and unlinked again; they stay in the order with the same price."""
    owner.put(
        f"/api/c/{s.cmp}/warehouse/items/{stock['filter']}/price",
        data={"amount": "350"},
        content_type="application/json",
        headers=key(),
    )
    op = _op(owner, s, [_work(owner, s, price="300")["id"]])
    body: dict[str, Any] = {
        "context_id": stock["ctx"],
        "item_id": stock["filter"],
        "asset_ids": [_asset(stock["filter"])],
    }
    if op["is_internal"]:
        body["for_vehicle_id"] = op["vehicle_card_id"]
    lines_url = f"/api/c/{s.cmp}/warehouse/orders/{op['order_id']}/sale-lines"
    added = post(owner, lines_url, body, key())
    assert added.status_code == 201, added.content
    line = added.json()["lines"][0]

    def goods() -> list[dict[str, Any]]:
        url = f"/api/c/{s.cmp}/service/operations/{op['id']}"
        found: list[dict[str, Any]] = owner.get(url).json()["operation"]["goods"]
        return found

    assert [(g["id"], g["job_id"]) for g in goods()] == [(line["id"], None)]
    job = op["jobs"][0]
    linked = post(
        owner,
        f"/api/c/{s.cmp}/service/jobs/{job['id']}/goods",
        {"line_id": line["id"], "expected_version": job["version"]},
        key(),
    )
    assert linked.status_code == 200, linked.content
    assert [(g["job_id"], g["amount"]) for g in goods()] == [(job["id"], line["amount"])]

    unlinked = post(owner, f"/api/c/{s.cmp}/service/goods/{line['id']}/unlink", {}, key())
    assert unlinked.status_code == 200, unlinked.content
    # Still in the order, reserved, with the same price.
    assert [(g["job_id"], g["state"], g["amount"]) for g in goods()] == [
        (None, "reserved", line["amount"])
    ]
    # Not linked any more: nothing to unlink.
    again = post(owner, f"/api/c/{s.cmp}/service/goods/{line['id']}/unlink", {}, key())
    assert again.status_code == 404


def test_goods_of_a_done_work_are_not_simply_cancelled(
    owner: Client, s: Scenario, stock: dict[str, Any]
) -> None:
    """Customer's decision of 22.11.2026: what a done work used is on the vehicle — its sale is
    not cancelled; it comes back only by a removal and a return with a valuation."""
    from c4rsys.warehouse.models import SaleLine

    owner.put(
        f"/api/c/{s.cmp}/warehouse/items/{stock['filter']}/price",
        data={"amount": "350"},
        content_type="application/json",
        headers=key(),
    )
    op = _op(owner, s, [_work(owner, s, price="300")["id"]])
    lines_url = f"/api/c/{s.cmp}/warehouse/orders/{op['order_id']}/sale-lines"
    body: dict[str, Any] = {
        "context_id": stock["ctx"],
        "item_id": stock["filter"],
        "asset_ids": [_asset(stock["filter"])],
    }
    line = post(owner, lines_url, body, key()).json()["lines"][0]
    assert line["installed"] is False
    job = op["jobs"][0]
    post(
        owner,
        f"/api/c/{s.cmp}/service/jobs/{job['id']}/goods",
        {"line_id": line["id"], "expected_version": job["version"]},
        key(),
    )
    op = _ok(_do(owner, s, op, "accept", accepted_at=_at(-3)))
    op = _ok(_job(owner, s, op["jobs"][0], "start", actual_start=_at(-2)))
    op = _ok(_job(owner, s, op["jobs"][0], "complete", actual_end=_at(-1), odometer="10500"))
    assert [g["installed"] for g in op["goods"]] == [True]
    # As after the closing of the order and its reopening: the sale is posted, the order open.
    SaleLine.objects.filter(pk=line["id"]).update(state=SaleLine.State.SOLD)
    refused = post(owner, f"{lines_url}/{line['id']}/cancel", {"reason": "mistake"}, key())
    assert _code(refused) == "SALE_LINE_INSTALLED"
    # Nor returned while it is on the vehicle: the service takes it off first.
    early = post(owner, f"{lines_url}/{line['id']}/return", {"amount": "100"}, key())
    assert _code(early) == "SALE_LINE_INSTALLED"
    taken = post(owner, f"/api/c/{s.cmp}/service/goods/{line['id']}/take-off", {}, key())
    assert taken.status_code == 200, taken.content
    assert [bool(g["taken_off_at"]) for g in taken.json()["operation"]["goods"]] == [True]
    twice = post(owner, f"/api/c/{s.cmp}/service/goods/{line['id']}/take-off", {}, key())
    assert _code(twice) == "SALE_LINE_STATE"
