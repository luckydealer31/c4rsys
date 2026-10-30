"""Discounts of an order and client groups (ТЗ § 12.5, § 6.2.14; decisions З11–З14; part А6.3)."""

from typing import Any

import pytest
from django.test import Client

from c4rsys.finance.models import ChargeEntry
from tests.accounts.helpers import post
from tests.companies.helpers import hire, key
from tests.rentals.scenario import Scenario
from tests.service.test_service import _at, _do, _job, _ok, _op, _order, _work

pytestmark = pytest.mark.django_db


def _discount(owner: Client, s: Scenario, **body: Any) -> dict[str, Any]:
    data = {"name": "Ten", "kind": "percent", "value": "10", "scope": "service", **body}
    created = post(owner, f"/api/c/{s.cmp}/discounts", data, key())
    assert created.status_code == 201, created.content
    found: dict[str, Any] = created.json()["discount"]
    return found


def _done_order(owner: Client, s: Scenario, prices: list[str]) -> dict[str, Any]:
    works = [_work(owner, s, name=f"W{p}", price=p) for p in prices]
    op = _op(owner, s, [w["id"] for w in works])
    op = _ok(_do(owner, s, op, "accept", accepted_at=_at(-5)))
    for index in range(len(prices)):
        op = _ok(_job(owner, s, op["jobs"][index], "start", actual_start=_at(-4)))
        op = _ok(
            _job(owner, s, op["jobs"][index], "complete", actual_end=_at(-3), odometer="10500")
        )
    return op


def _apply(client: Client, s: Scenario, order_id: str, **body: Any) -> Any:
    return post(client, f"/api/c/{s.cmp}/orders/{order_id}/discounts", body, key())


def _state(client: Client, s: Scenario, order_id: str) -> dict[str, Any]:
    found: dict[str, Any] = client.get(f"/api/c/{s.cmp}/orders/{order_id}/discounts").json()
    return found


def test_position_and_order_discounts_do_not_add_up(owner: Client, s: Scenario) -> None:
    op = _done_order(owner, s, ["500", "300"])
    order_id = op["order_id"]
    ten = _discount(owner, s)
    fixed = _discount(
        owner, s, name="Minus 200", kind="amount", value="200", currency="THB", scope="order"
    )
    first = op["jobs"][0]

    own = _apply(
        owner,
        s,
        order_id,
        discount_id=ten["id"],
        position_type="service_job",
        position_id=first["id"],
    )
    assert own.status_code == 200, own.content
    assert _order(owner, s, order_id)["amounts"]["THB"]["charged"] == "750.00"

    # § 12.5.3: never resolved automatically.
    conflict = _apply(owner, s, order_id, discount_id=fixed["id"])
    assert conflict.json()["error"]["code"] == "DISCOUNT_CONFLICT"
    kept = _apply(owner, s, order_id, discount_id=fixed["id"], conflict="keep_positions")
    assert kept.status_code == 200, kept.content
    by_job = {p["id"]: p for p in kept.json()["positions"]}
    # The position keeps its 10 %, the order's 200 goes to the rest (capped by 300).
    assert by_job[first["id"]]["discount"] == "50.00"
    assert by_job[op["jobs"][1]["id"]]["discount"] == "200.00"
    assert _order(owner, s, order_id)["amounts"]["THB"]["charged"] == "550.00"

    removed = _apply(owner, s, order_id, discount_id=fixed["id"], conflict="remove_positions")
    shares = sorted(p["discount"] for p in removed.json()["positions"])
    # 200 spread in proportion 500 : 300.
    assert shares == ["125.00", "75.00"]
    assert _order(owner, s, order_id)["amounts"]["THB"]["charged"] == "600.00"


def test_recalculated_position_recalculates_its_discount(owner: Client, s: Scenario) -> None:
    op = _done_order(owner, s, ["500"])
    order_id = op["order_id"]
    ten = _discount(owner, s)
    job = op["jobs"][0]
    _apply(
        owner,
        s,
        order_id,
        discount_id=ten["id"],
        position_type="service_job",
        position_id=job["id"],
    )
    changed = _job(owner, s, job, "price", price="1000")
    assert changed.status_code == 200, changed.content
    # З14: a percent counts from the new amount.
    assert _order(owner, s, order_id)["amounts"]["THB"]["charged"] == "900.00"
    discount = ChargeEntry.objects.filter(source_type="discount", source_id=job["id"])
    assert sum(e.amount for e in discount) == -100


def test_limit_groups_and_rights(owner: Client, s: Scenario) -> None:
    op = _done_order(owner, s, ["500"])
    order_id = op["order_id"]
    twenty = _discount(owner, s, name="Twenty", value="20")
    rights = [
        {"type": "grant", "permission": p}
        for p in ("order.view", "order.edit", "order.discount", "service.view")
    ]
    manager, employment = hire(owner, s.cmp, "manager@example.com", rights)
    body = {
        "discount_id": twenty["id"],
        "position_type": "service_job",
        "position_id": op["jobs"][0]["id"],
    }
    # § 12.5.5: without a limit nothing is applied; over it — refused.
    assert _apply(manager, s, order_id, **body).json()["error"]["code"] == (
        "DISCOUNT_LIMIT_NOT_SET"
    )
    limit_url = f"/api/c/{s.cmp}/employees/{employment}/discount-limit"
    response = owner.put(
        limit_url, data={"percent": "15"}, content_type="application/json", headers=key()
    )
    assert response.status_code == 200, response.content
    assert _apply(manager, s, order_id, **body).json()["error"]["code"] == "DISCOUNT_OVER_LIMIT"

    # § 12.5.2: a discount only for a group is offered to its members only.
    group = post(owner, f"/api/c/{s.cmp}/client-groups", {"name": "VIP"}, key()).json()["group"]
    vip = _discount(owner, s, name="VIP 5", value="5", client_group_ids=[group["id"]])
    assert vip["id"] not in [d["id"] for d in _state(owner, s, order_id)["offered"]]
    owner.put(
        f"/api/c/{s.cmp}/clients/{s.client}/groups",
        data={"group_ids": [group["id"]]},
        content_type="application/json",
        headers=key(),
    )
    assert vip["id"] in [d["id"] for d in _state(owner, s, order_id)["offered"]]
    body["discount_id"] = vip["id"]
    assert _apply(manager, s, order_id, **body).status_code == 200
