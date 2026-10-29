"""Delivery, part Д4: corrections and annulment of a finished move (ТЗ § 15.1.4, § 15.3.4–15.3.8;
customer's decision Д-11 of 29.10.2026)."""

from typing import Any

import pytest
from django.test import Client

from c4rsys.fleet.models import VehicleCard
from tests.accounts.helpers import post
from tests.companies.helpers import hire, key
from tests.delivery.test_deliveries import _at, _code, _create, _do, _ok
from tests.rentals.scenario import NOW, Scenario, issue

pytestmark = pytest.mark.django_db


def _driver(owner: Client, s: Scenario) -> str:
    driver: str = owner.get(f"/api/c/{s.cmp}/deliveries/drivers").json()["drivers"][0]["id"]
    return driver


def _moved(owner: Client, s: Scenario, to: dict[str, Any], **body: Any) -> dict[str, Any]:
    d = _ok(_create(owner, s, to, driver_id=_driver(owner, s), **body), 201)
    d = _ok(_do(owner, s, d, "start", actual_start=_at(-3)))
    result = "accepted_in_location" if to["kind"] == "location" else "handed_to_client"
    return _ok(_do(owner, s, d, "finish", actual_end=_at(-2), result=result))


def _location(s: Scenario) -> str:
    return str(VehicleCard.objects.get(pk=s.vehicle).accounting_location_id)


def test_correct_result_moves_the_binding_back(owner: Client, s: Scenario, patong: str) -> None:
    d = _moved(owner, s, {"kind": "location", "location_id": patong})
    assert _location(s) == patong
    assert _code(_do(owner, s, d, "correct", result="not_handed")) == "FIELD_REQUIRED"
    d = _ok(_do(owner, s, d, "correct", result="not_handed", reason="Wrong result"))
    # The vehicle was not accepted in B after all: it stays bound to A (§ 15.1.4).
    assert _location(s) == s.location
    d = _ok(
        _do(owner, s, d, "correct", result="accepted_in_location", actual_end=_at(-1), reason="Fix")
    )
    assert _location(s) == patong and d["actual_end"] == _at(-1)


def test_later_operation_blocks_the_correction(owner: Client, s: Scenario, patong: str) -> None:
    d = _moved(owner, s, {"kind": "location", "location_id": patong})
    issued = issue(owner, s, actual_start=NOW.isoformat(), issue_location_id=patong)
    assert issued.status_code == 201, issued.content
    refused = _do(owner, s, d, "correct", actual_end=_at(-1), reason="Fix")
    # § 15.3.4: the later rental is annulled first.
    assert _code(refused) == "DELIVERY_HAS_LATER_OPERATIONS"
    assert refused.json()["error"]["params"]["operations"][0]["kind"] == "rental"
    assert _code(_do(owner, s, d, "annul", reason="Wrong")) == "DELIVERY_HAS_LATER_OPERATIONS"


def test_annul_reverses_the_charge(owner: Client, s: Scenario, patong: str) -> None:
    order = post(owner, f"/api/c/{s.cmp}/orders", {"client_id": s.client}, key()).json()["order"]
    d = _moved(owner, s, {"kind": "other", "address": "Kata"}, order_id=order["id"], price="300")
    assert d["charged"] == "300"
    d = _ok(_do(owner, s, d, "annul", reason="Entered by mistake"))
    # § 15.3.5: the wrong charge is corrected by a reversal; the history stays.
    assert d["state"] == "annulled" and d["charged"] == "0"
    charged = owner.get(f"/api/c/{s.cmp}/orders/{order['id']}").json()["order"]["amounts"]
    assert charged["THB"]["charged"] == "0.00"


def test_annul_of_a_move_rebinds_and_needs_the_right(
    owner: Client, s: Scenario, patong: str
) -> None:
    clerk, _ = hire(
        owner,
        s.cmp,
        "clerk@example.com",
        [{"type": "grant", "permission": p} for p in ("delivery.view", "delivery.finish")],
    )
    d = _moved(owner, s, {"kind": "location", "location_id": patong})
    assert _do(clerk, s, d, "annul", reason="x").status_code == 403
    _ok(_do(owner, s, d, "annul", reason="Never happened"))
    assert _location(s) == s.location
