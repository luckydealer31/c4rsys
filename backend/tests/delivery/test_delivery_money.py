"""Delivery, part Д2: the catalog, the charge to the client and the expenses of a trip
(ТЗ § 15.2.2–15.2.3, § 15.2.6; customer's decisions Д-4…Д-6 of 29.10.2026)."""

from typing import Any

import pytest
from django.test import Client

from tests.accounts.helpers import post
from tests.companies.helpers import hire, key
from tests.delivery.test_deliveries import _at, _code, _create, _do, _ok
from tests.rentals.scenario import NOW, Scenario

pytestmark = pytest.mark.django_db


def _service(owner: Client, s: Scenario, price: str = "300") -> dict[str, Any]:
    created = post(
        owner, f"/api/c/{s.cmp}/delivery-services", {"name": "Kata", "price": price}, key()
    )
    assert created.status_code == 201, created.content
    service: dict[str, Any] = created.json()["service"]
    return service


def _client_order(owner: Client, s: Scenario) -> str:
    order_id: str = post(owner, f"/api/c/{s.cmp}/orders", {"client_id": s.client}, key()).json()[
        "order"
    ]["id"]
    return order_id


def _charged(owner: Client, s: Scenario, order_id: str) -> str | None:
    amounts = owner.get(f"/api/c/{s.cmp}/orders/{order_id}").json()["order"]["amounts"]
    charged: str | None = amounts.get("THB", {}).get("charged")
    return charged


def test_charged_at_once_and_zero_when_cancelled(owner: Client, s: Scenario) -> None:
    service = _service(owner, s)
    order = _client_order(owner, s)
    d = _ok(
        _create(
            owner, s, {"kind": "other", "address": "Kata"}, order_id=order, service_id=service["id"]
        ),
        201,
    )
    assert d["price"] == "300" and _charged(owner, s, order) == "300.00"
    _ok(_do(owner, s, d, "cancel", reason="Client called"))
    # Decision Д-4: a delivery cancelled before the start is not charged.
    assert _charged(owner, s, order) == "0.00"


def test_refusal_keeps_the_charge(owner: Client, s: Scenario) -> None:
    order = _client_order(owner, s)
    me = owner.get(f"/api/c/{s.cmp}/deliveries/drivers").json()["drivers"][0]["id"]
    d = _ok(
        _create(
            owner,
            s,
            {"kind": "other", "address": "Kata"},
            order_id=order,
            price="450",
            driver_id=me,
        ),
        201,
    )
    d = _ok(_do(owner, s, d, "start", actual_start=_at(-1)))
    _ok(_do(owner, s, d, "finish", actual_end=NOW.isoformat(), result="not_handed"))
    # § 15.4.1: the delivery made keeps its charge.
    assert _charged(owner, s, order) == "450.00"


def test_override_needs_the_price_right(owner: Client, s: Scenario) -> None:
    service = _service(owner, s)
    order = _client_order(owner, s)
    rights = ("delivery.create", "delivery.view", "order.edit", "order.view")
    clerk, _ = hire(
        owner, s.cmp, "clerk@example.com", [{"type": "grant", "permission": p} for p in rights]
    )
    to = {"kind": "other", "address": "Kata"}
    refused = _create(clerk, s, to, order_id=order, service_id=service["id"], price="100")
    assert refused.status_code == 403
    _ok(_create(clerk, s, to, order_id=order, service_id=service["id"]), 201)
    # A manual price without the catalog is entered at creation (§ 15.2.6).
    _ok(
        _create(
            clerk, s, to, order_id=order, price="200", planned_start=_at(5), planned_end=_at(6)
        ),
        201,
    )


def test_internal_move_takes_no_price_and_attach_sets_it(owner: Client, s: Scenario) -> None:
    to = {"kind": "other", "address": "Kata"}
    assert _code(_create(owner, s, to, price="100")) == "DELIVERY_INTERNAL_NO_PRICE"
    d = _ok(_create(owner, s, to), 201)
    order = _client_order(owner, s)
    d = _ok(_do(owner, s, d, "attach", order_id=order, price="350"))
    assert d["price"] == "350" and _charged(owner, s, order) == "350.00"
    d = _ok(_do(owner, s, d, "price", price="250"))
    assert _charged(owner, s, order) == "250.00"


def test_expense_is_a_payout_linked_to_the_trip(owner: Client, s: Scenario) -> None:
    account = post(
        owner,
        f"/api/c/{s.cmp}/cash-accounts",
        {"name": "Cash", "type": "cash", "currency": "THB"},
        key(),
    ).json()["cash_account"]["id"]
    article = next(
        a["id"]
        for a in owner.get(f"/api/c/{s.cmp}/finance-articles").json()["articles"]
        if a["kind"] == "expense"
    )
    d = _ok(_create(owner, s, {"kind": "other", "address": "Kata"}), 201)
    body = {"cash_account_id": account, "amount": "120", "currency": "THB", "article_id": article}
    added = post(owner, f"/api/c/{s.cmp}/deliveries/{d['id']}/expenses", body, key())
    d = _ok(added, 201)
    assert [e["amount"] for e in d["expenses"]] == ["120"]
    # The expense does not change the client's price (§ 15.2.6).
    assert d["price"] is None
