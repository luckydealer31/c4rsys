"""Manual and preliminary orders, the responsible employee, cancellation of an order, annulment
and transfer of a rental (ТЗ § 12.1.7, § 12.1.10, § 12.2.1–12.2.2, § 12.3.7; customer's decisions
З2–З7 of 28.10.2026)."""

from collections.abc import Iterator
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from django.test import Client

from c4rsys.rentals import bookings as bookings_module
from c4rsys.rentals import extra_lines as lines_module
from c4rsys.rentals import issue as issue_module
from c4rsys.rentals import order_links as links_module
from c4rsys.rentals import rentals as rentals_module
from c4rsys.rentals import returns as returns_module
from c4rsys.rentals import views as views_module
from tests.accounts.helpers import post
from tests.companies.helpers import hire, key
from tests.rentals.scenario import NOW, Scenario, add_client, build, issue

pytestmark = pytest.mark.django_db


@pytest.fixture
def s(owner: Client) -> Scenario:
    return build(owner)


@pytest.fixture(autouse=True)
def frozen() -> Iterator[None]:
    clock = SimpleNamespace(now=lambda: NOW)
    with (
        mock.patch.object(issue_module, "timezone", clock),
        mock.patch.object(returns_module, "timezone", clock),
        mock.patch.object(lines_module, "timezone", clock),
        mock.patch.object(bookings_module, "timezone", clock),
        mock.patch.object(rentals_module, "timezone", clock),
        mock.patch.object(views_module, "timezone", clock),
        mock.patch.object(links_module, "timezone", clock),
    ):
        yield


def _days(days: float) -> str:
    return (NOW + timedelta(days=days)).isoformat()


def _code(response: Any) -> str:
    code: str = response.json()["error"]["code"]
    return code


def _order(owner: Client, s: Scenario, order_id: str) -> dict[str, Any]:
    order: dict[str, Any] = owner.get(f"/api/c/{s.cmp}/orders/{order_id}").json()["order"]
    return order


def _create(owner: Client, s: Scenario, **body: Any) -> Any:
    return post(owner, f"/api/c/{s.cmp}/orders", body, key())


def _action(owner: Client, s: Scenario, order: dict[str, Any], action: str, **body: Any) -> Any:
    data = {"expected_version": order["version"], **body}
    return post(owner, f"/api/c/{s.cmp}/orders/{order['id']}/{action}", data, key())


def _propose(owner: Client, s: Scenario, order_id: str, start: float = 1, end: float = 3) -> Any:
    body = {
        "vehicle_card_id": s.vehicle,
        "tariff_id": s.tariff,
        "planned_start": _days(start),
        "planned_end": _days(end),
        "issue_location_id": s.location,
    }
    return post(owner, f"/api/c/{s.cmp}/orders/{order_id}/rental-proposals", body, key())


def _book(owner: Client, s: Scenario, start: float, end: float) -> dict[str, Any]:
    body = {
        "vehicle_card_id": s.vehicle,
        "client_id": s.client,
        "tariff_id": s.tariff,
        "issue_location_id": s.location,
        "planned_start": _days(start),
        "planned_end": _days(end),
    }
    response = post(owner, f"/api/c/{s.cmp}/bookings", body, key())
    assert response.status_code == 201, response.content
    rental: dict[str, Any] = response.json()["rental"]
    return rental


def _rental(owner: Client, s: Scenario, rental_id: str) -> dict[str, Any]:
    rental: dict[str, Any] = owner.get(f"/api/c/{s.cmp}/rentals/{rental_id}").json()["rental"]
    return rental


def _account(owner: Client, s: Scenario) -> str:
    body = {"name": "Cash", "type": "cash", "currency": "THB"}
    account: str = post(owner, f"/api/c/{s.cmp}/cash-accounts", body, key()).json()["cash_account"][
        "id"
    ]
    return account


def _pay(owner: Client, s: Scenario, order_id: str, amount: str) -> None:
    body = {
        "cash_account_id": _account(owner, s),
        "client_id": s.client,
        "amount": amount,
        "currency": "THB",
        "allocations": [{"order_id": order_id, "amount": amount}],
    }
    response = post(owner, f"/api/c/{s.cmp}/payments/cash", body, key())
    assert response.status_code == 201, response.content


# --- Manual and preliminary orders --------------------------------------------------------------


def test_manual_order_for_a_client_or_the_company(owner: Client, s: Scenario) -> None:
    created = _create(owner, s, client_id=s.client)
    assert created.status_code == 201, created.content
    order = created.json()["order"]
    assert (order["state"], order["client_id"], order["is_internal"]) == (
        "ready_to_close",
        s.client,
        False,
    )
    internal = _create(owner, s, internal=True).json()["order"]
    assert internal["is_internal"] is True
    # З3: a client or the company itself, not both and not neither.
    assert _code(_create(owner, s)) == "CLIENT_REQUIRED"
    assert _code(_create(owner, s, client_id=s.client, internal=True)) == "CLIENT_REQUIRED"


def test_preliminary_order_estimates_and_forming_books(owner: Client, s: Scenario) -> None:
    order = _create(owner, s, client_id=s.client, preliminary=True).json()["order"]
    assert order["state"] == "preliminary"
    proposal = _propose(owner, s, order["id"])
    assert proposal.status_code == 201, proposal.content
    proposal_data = proposal.json()["proposal"]
    assert proposal_data["estimate"] == {"amount": "2000.00", "currency": "THB"}

    # З2: a preliminary order charges nothing; its cost is estimated.
    order = _order(owner, s, order["id"])
    assert order["amounts"] == {}
    assert order["estimated"] == {"THB": "2000.00"}
    # The proposal holds nothing: a booking of the same period is possible.
    assert proposal_data["conflicts"] == []
    rentals = owner.get(f"/api/c/{s.cmp}/rentals").json()["rentals"]
    assert all(r["id"] != proposal_data["id"] for r in rentals)

    formed = _action(owner, s, order, "form")
    assert formed.status_code == 200, formed.content
    order = formed.json()["order"]
    assert order["state"] != "preliminary"
    assert order["amounts"]["THB"]["charged"] == "2000.00"
    rental = _rental(owner, s, proposal_data["id"])
    assert rental["state"] == "booked"
    assert rental["order_id"] == order["id"]


def test_forming_refuses_a_taken_vehicle(owner: Client, s: Scenario) -> None:
    order = _create(owner, s, client_id=s.client, preliminary=True).json()["order"]
    _propose(owner, s, order["id"], 1, 3)
    _book(owner, s, 2, 4)
    refused = _action(owner, s, _order(owner, s, order["id"]), "form")
    assert _code(refused) == "VEHICLE_NOT_AVAILABLE"
    assert _order(owner, s, order["id"])["state"] == "preliminary"


def test_preliminary_order_takes_no_payment_and_no_issue(owner: Client, s: Scenario) -> None:
    order = _create(owner, s, client_id=s.client, preliminary=True).json()["order"]
    body = {
        "cash_account_id": _account(owner, s),
        "client_id": s.client,
        "amount": "100",
        "currency": "THB",
        "allocations": [{"order_id": order["id"], "amount": "100"}],
    }
    paid = post(owner, f"/api/c/{s.cmp}/payments/cash", body, key())
    assert _code(paid) == "ORDER_PRELIMINARY"
    issued = issue(owner, s, order_id=order["id"])
    assert _code(issued) == "ORDER_PRELIMINARY"


def test_withdrawn_proposal_and_proposal_only_in_preliminary(owner: Client, s: Scenario) -> None:
    order = _create(owner, s, client_id=s.client, preliminary=True).json()["order"]
    proposal = _propose(owner, s, order["id"]).json()["proposal"]
    withdrawn = post(
        owner,
        f"/api/c/{s.cmp}/rentals/{proposal['id']}/withdraw-proposal",
        {"expected_version": proposal["version"]},
        key(),
    )
    assert withdrawn.status_code == 200, withdrawn.content
    assert _order(owner, s, order["id"])["estimated"] == {}
    regular = _create(owner, s, client_id=s.client).json()["order"]
    assert _code(_propose(owner, s, regular["id"])) == "ORDER_NOT_PRELIMINARY"


# --- Responsible employee ---------------------------------------------------------------------


def test_responsible_is_changed_with_order_edit(owner: Client, s: Scenario) -> None:
    order = _create(owner, s, client_id=s.client).json()["order"]
    clerk, employment = hire(
        owner, s.cmp, "clerk@example.com", [{"type": "grant", "permission": "order.view"}]
    )
    changed = _action(owner, s, order, "responsible", employment_id=employment)
    assert changed.status_code == 200, changed.content
    order = changed.json()["order"]
    assert order["responsible_employment_id"] == employment
    # The responsible employee gets no rights by being responsible (§ 12.1.10).
    refused = _action(clerk, s, order, "responsible", employment_id=employment)
    assert refused.status_code == 403


# --- Cancellation of the order ---------------------------------------------------------------


def test_cancel_order_cancels_bookings_and_keeps_payments(owner: Client, s: Scenario) -> None:
    booking = _book(owner, s, 1, 3)
    _pay(owner, s, booking["order_id"], "500")
    order = _order(owner, s, booking["order_id"])
    assert _code(_action(owner, s, order, "cancel", reason=" ")) == "FIELD_REQUIRED"
    cancelled = _action(owner, s, order, "cancel", reason="Client changed plans")
    assert cancelled.status_code == 200, cancelled.content
    order = cancelled.json()["order"]
    assert order["state"] == "cancelled"
    assert order["cancel_reason"] == "Client changed plans"
    # З4: money paid stays as an overpayment of the order.
    assert order["amounts"]["THB"]["charged"] == "0.00"
    assert order["amounts"]["THB"]["overpaid"] == "500.00"
    assert _rental(owner, s, booking["id"])["state"] == "cancelled"
    # The period is free again.
    _book(owner, s, 1, 3)


def test_cancel_order_refuses_an_issued_rental(owner: Client, s: Scenario) -> None:
    rental = issue(owner, s).json()["rental"]
    order = _order(owner, s, rental["order_id"])
    refused = _action(owner, s, order, "cancel", reason="Mistake")
    assert _code(refused) == "ORDER_HAS_ACTIVE_RENTAL"


def test_cancel_order_needs_its_right(owner: Client, s: Scenario) -> None:
    booking = _book(owner, s, 1, 3)
    clerk, _ = hire(
        owner, s.cmp, "clerk@example.com", [{"type": "grant", "permission": "order.view"}]
    )
    refused = _action(clerk, s, _order(owner, s, booking["order_id"]), "cancel", reason="x")
    assert refused.status_code == 403


# --- Annulment and transfer of a rental -------------------------------------------------------


def test_annulled_rental_zeroes_its_charges_and_lets_the_order_close(
    owner: Client, s: Scenario
) -> None:
    rental = issue(owner, s, planned_end=_days(1)).json()["rental"]
    assert _order(owner, s, rental["order_id"])["amounts"]["THB"]["charged"] == "3000.00"
    url = f"/api/c/{s.cmp}/rentals/{rental['id']}/annul"
    assert _code(post(owner, url, {"expected_version": rental["version"]}, key())) == (
        "FIELD_REQUIRED"
    )
    annulled = post(
        owner, url, {"reason": "Wrong vehicle", "expected_version": rental["version"]}, key()
    )
    assert annulled.status_code == 200, annulled.content
    assert annulled.json()["rental"]["state"] == "annulled"
    order = _order(owner, s, rental["order_id"])
    assert order["amounts"]["THB"]["charged"] == "0.00"
    assert order["state"] == "ready_to_close"
    # The vehicle is free for a new issue.
    again = issue(owner, s, actual_start=NOW.isoformat())
    assert again.status_code == 201, again.content


def test_transfer_to_another_order_of_the_client(owner: Client, s: Scenario) -> None:
    rental = issue(owner, s, planned_end=_days(1)).json()["rental"]
    _pay(owner, s, rental["order_id"], "1000")
    target = _create(owner, s, client_id=s.client).json()["order"]
    url = f"/api/c/{s.cmp}/rentals/{rental['id']}/transfer"
    moved = post(
        owner, url, {"order_id": target["id"], "expected_version": rental["version"]}, key()
    )
    assert moved.status_code == 200, moved.content
    assert moved.json()["rental"]["order_id"] == target["id"]
    source = _order(owner, s, rental["order_id"])
    # З6: the charges go with the rental; the payment stays where it was.
    assert source["amounts"]["THB"]["charged"] == "0.00"
    assert source["amounts"]["THB"]["overpaid"] == "1000.00"
    assert _order(owner, s, target["id"])["amounts"]["THB"]["charged"] == "3000.00"


def test_transfer_to_a_new_order_and_refusals(owner: Client, s: Scenario) -> None:
    rental = issue(owner, s, planned_end=_days(1)).json()["rental"]
    url = f"/api/c/{s.cmp}/rentals/{rental['id']}/transfer"
    other = _create(owner, s, client_id=add_client(owner, s.cmp, "Anna", "+66800000000"))
    body = {"order_id": other.json()["order"]["id"], "expected_version": rental["version"]}
    assert _code(post(owner, url, body, key())) == "ORDER_CLIENT_MISMATCH"
    body = {"order_id": rental["order_id"], "expected_version": rental["version"]}
    assert _code(post(owner, url, body, key())) == "TRANSFER_SAME_ORDER"
    moved = post(owner, url, {"expected_version": rental["version"]}, key())
    assert moved.status_code == 200, moved.content
    new_order = _order(owner, s, moved.json()["rental"]["order_id"])
    assert new_order["id"] != rental["order_id"]
    assert new_order["client_id"] == s.client
    assert new_order["amounts"]["THB"]["charged"] == "3000.00"


def test_annul_and_transfer_need_their_rights(owner: Client, s: Scenario) -> None:
    rental = issue(owner, s).json()["rental"]
    rights = [
        {"type": "grant", "permission": p} for p in ("rental.view", "order.view", "order.create")
    ]
    clerk, _ = hire(owner, s.cmp, "clerk@example.com", rights)
    body = {"reason": "x", "expected_version": rental["version"]}
    assert post(clerk, f"/api/c/{s.cmp}/rentals/{rental['id']}/annul", body, key()).status_code == (
        403
    )
    body = {"expected_version": rental["version"]}
    moved = post(clerk, f"/api/c/{s.cmp}/rentals/{rental['id']}/transfer", body, key())
    assert moved.status_code == 403


def test_services_of_a_preliminary_order(owner: Client, s: Scenario) -> None:
    service = post(
        owner,
        f"/api/c/{s.cmp}/extra-services",
        {"name": "Delivery", "pricing": "once", "price": "300"},
        key(),
    ).json()["service"]
    order = _create(owner, s, client_id=s.client, preliminary=True).json()["order"]
    body = {"service_id": service["id"], "location_id": s.location, "order_id": order["id"]}
    added = post(owner, f"/api/c/{s.cmp}/service-lines", body, key())
    assert added.status_code == 201, added.content
    line = added.json()["line"]
    order = _order(owner, s, order["id"])
    assert (order["amounts"], order["estimated"]) == ({}, {"THB": "300.00"})
    # Nothing starts in a preliminary order (§ 12.2.2).
    started = post(
        owner,
        f"/api/c/{s.cmp}/service-lines/{line['id']}/start",
        {"expected_version": line["version"]},
        key(),
    )
    assert _code(started) == "ORDER_PRELIMINARY"
    order = _action(owner, s, order, "form").json()["order"]
    assert order["amounts"]["THB"]["charged"] == "300.00"
    cancelled = _action(owner, s, order, "cancel", reason="No longer needed")
    assert cancelled.status_code == 200, cancelled.content
    assert cancelled.json()["order"]["amounts"]["THB"]["charged"] == "0.00"
