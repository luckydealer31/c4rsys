"""Service works: catalog, rates, posts, operations and works (ТЗ гл. 16; customer's decisions
С-1…С-7 of 29.10.2026)."""

from collections.abc import Iterator
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from django.test import Client

from c4rsys.rentals import bookings as bookings_module
from c4rsys.rentals import issue as issue_module
from c4rsys.rentals import rentals as rentals_module
from c4rsys.rentals import views as rental_views_module
from c4rsys.service import operations as operations_module
from c4rsys.service.models import SystemWork
from tests.accounts.helpers import post
from tests.companies.helpers import hire, key
from tests.rentals.scenario import NOW, Scenario, add_location, add_vehicle, build, issue

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def frozen() -> Iterator[None]:
    clock = SimpleNamespace(now=lambda: NOW)
    with (
        mock.patch.object(issue_module, "timezone", clock),
        mock.patch.object(bookings_module, "timezone", clock),
        mock.patch.object(rentals_module, "timezone", clock),
        mock.patch.object(rental_views_module, "timezone", clock),
        mock.patch.object(operations_module, "timezone", clock),
    ):
        yield


@pytest.fixture
def s(owner: Client) -> Scenario:
    scenario = build(owner)
    post(
        owner,
        f"/api/c/{scenario.cmp}/locations/{scenario.location}/contexts",
        {"type": "service"},
        key(),
    )
    return scenario


def _at(hours: float) -> str:
    return (NOW + timedelta(hours=hours)).isoformat()


def _code(response: Any) -> str:
    code: str = response.json()["error"]["code"]
    return code


def _work(owner: Client, s: Scenario, **body: Any) -> dict[str, Any]:
    data = {"name": "Oil change", "pricing": "fixed", "price": "500", **body}
    response = post(owner, f"/api/c/{s.cmp}/service/works", data, key())
    assert response.status_code == 201, response.content
    work: dict[str, Any] = response.json()["work"]
    return work


def _operation(owner: Client, s: Scenario, works: list[str], **body: Any) -> Any:
    data = {
        "vehicle_card_id": s.vehicle,
        "location_id": s.location,
        "reason": "Scheduled service",
        "client_id": s.client,
        "work_ids": works,
        **body,
    }
    return post(owner, f"/api/c/{s.cmp}/service/operations", data, key())


def _op(owner: Client, s: Scenario, works: list[str], **body: Any) -> dict[str, Any]:
    response = _operation(owner, s, works, **body)
    assert response.status_code == 201, response.content
    op: dict[str, Any] = response.json()["operation"]
    return op


def _do(client: Client, s: Scenario, op: dict[str, Any], action: str, **body: Any) -> Any:
    data = {"expected_version": op["version"], **body}
    return post(client, f"/api/c/{s.cmp}/service/operations/{op['id']}/{action}", data, key())


def _job(client: Client, s: Scenario, job: dict[str, Any], action: str, **body: Any) -> Any:
    data = {"expected_version": job["version"], **body}
    return post(client, f"/api/c/{s.cmp}/service/jobs/{job['id']}/{action}", data, key())


def _ok(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, response.content
    op: dict[str, Any] = response.json()["operation"]
    return op


def _order(owner: Client, s: Scenario, order_id: str) -> dict[str, Any]:
    order: dict[str, Any] = owner.get(f"/api/c/{s.cmp}/orders/{order_id}").json()["order"]
    return order


def _done(owner: Client, s: Scenario, op: dict[str, Any], index: int = 0) -> dict[str, Any]:
    op = _ok(_job(owner, s, op["jobs"][index], "start", actual_start=_at(-2)))
    return _ok(_job(owner, s, op["jobs"][index], "complete", actual_end=_at(-1), odometer="10500"))


def test_system_reference_has_three_languages(owner: Client, s: Scenario) -> None:
    items = owner.get(f"/api/c/{s.cmp}/service/system-works").json()["system_works"]
    assert len(items) == SystemWork.objects.count() >= 40
    assert all(set(i["names"]) == {"en", "ru", "th"} for i in items)


def test_catalog_and_rate_priority(owner: Client, s: Scenario) -> None:
    linked = _work(owner, s, system_work="engine_oil", name="Масло")
    assert linked["system_names"]["ru"] == "Замена моторного масла"
    assert linked["currency"] == "THB"
    hours = _work(owner, s, name="Diagnostics", pricing="hours", price=None, hours="1.5")
    unknown = _work(owner, s, name="Repair", price=None)

    # No rate yet: the price is not known (§ 16.1.4).
    op = _op(owner, s, [hours["id"], unknown["id"]])
    assert [j["price"] for j in op["jobs"]] == [None, None]

    owner.put(
        f"/api/c/{s.cmp}/service/rates",
        data={"rate": "800"},
        content_type="application/json",
        headers=key(),
    )
    owner.put(
        f"/api/c/{s.cmp}/service/rates",
        data={"rate": "1000", "location_id": s.location},
        content_type="application/json",
        headers=key(),
    )
    op = _ok(_do(owner, s, op, "jobs", work_id=hours["id"]))
    assert op["jobs"][-1]["price"] == "1500"
    assert op["jobs"][-1]["rate_source"] == "location"

    changed = owner.patch(
        f"/api/c/{s.cmp}/service/works/{hours['id']}",
        data={
            "name": "Diagnostics",
            "pricing": "hours",
            "hours": "1.5",
            "rate": "600",
            "expected_version": hours["version"],
        },
        content_type="application/json",
        headers=key(),
    )
    assert changed.status_code == 200, changed.content
    op = _ok(_do(owner, s, op, "jobs", work_id=hours["id"]))
    assert op["jobs"][-1]["price"] == "900"
    assert op["jobs"][-1]["rate_source"] == "work"
    # Added works keep their terms (§ 16.1.3).
    assert op["jobs"][-2]["price"] == "1500"


def test_lifecycle_charges_and_closing(owner: Client, s: Scenario) -> None:
    oil = _work(owner, s)
    brakes = _work(owner, s, name="Brakes", price="1200")
    op = _op(owner, s, [oil["id"], brakes["id"]], planned_start=_at(-4), planned_end=_at(4))
    order = _order(owner, s, op["order_id"])
    assert order["client_id"] == s.client
    assert order["amounts"] == {}

    # Works do not start before the vehicle is accepted (§ 16.2.8).
    early = _job(owner, s, op["jobs"][0], "start", actual_start=_at(-2))
    assert _code(early) == "SERVICE_VEHICLE_NOT_ACCEPTED"

    op = _ok(_do(owner, s, op, "accept", accepted_at=_at(-3)))
    assert op["state"] == "accepted"
    op = _done(owner, s, op)
    assert op["state"] == "in_progress"
    assert op["jobs"][0]["state"] == "done"
    assert op["jobs"][0]["executor_id"] is not None
    assert _order(owner, s, op["order_id"])["amounts"]["THB"]["charged"] == "500.00"

    # Not every work has a result yet (§ 16.7.2).
    assert _code(_do(owner, s, op, "finish")) == "SERVICE_JOBS_NOT_FINISHED"
    op = _ok(_job(owner, s, op["jobs"][1], "cancel", reason="Not needed"))
    op = _ok(_do(owner, s, op, "finish"))
    assert op["state"] == "works_done"

    order = _order(owner, s, op["order_id"])
    assert order["amounts"]["THB"]["charged"] == "500.00"
    assert order["closing_conditions"] == []
    closed = post(
        owner,
        f"/api/c/{s.cmp}/orders/{order['id']}/close",
        {"expected_version": order["version"]},
        key(),
    )
    assert closed.status_code == 200, closed.content


def test_work_without_price_blocks_closing(owner: Client, s: Scenario) -> None:
    repair = _work(owner, s, name="Repair", price=None)
    op = _op(owner, s, [repair["id"]])
    op = _ok(_do(owner, s, op, "accept", accepted_at=_at(-3)))
    op = _done(owner, s, op)
    op = _ok(_do(owner, s, op, "finish"))
    order = _order(owner, s, op["order_id"])
    assert order["amounts"] == {}
    assert [c["condition"] for c in order["closing_conditions"]] == ["service_price_missing"]

    # An explicit zero differs from no price and is enough (§ 16.1.4).
    op = _ok(_job(owner, s, op["jobs"][0], "price", price="0"))
    assert _order(owner, s, op["order_id"])["closing_conditions"] == []
    op = _ok(_job(owner, s, op["jobs"][0], "price", price="700"))
    assert _order(owner, s, op["order_id"])["amounts"]["THB"]["charged"] == "700.00"


def test_internal_order_takes_no_selling_price(owner: Client, s: Scenario) -> None:
    repair = _work(owner, s, name="Repair", price=None)
    op = _op(owner, s, [repair["id"]], client_id=None, internal=True)
    op = _ok(_do(owner, s, op, "accept", accepted_at=_at(-3)))
    op = _done(owner, s, op)
    op = _ok(_do(owner, s, op, "finish"))
    order = _order(owner, s, op["order_id"])
    assert order["is_internal"] is True
    assert order["amounts"] == {}
    assert order["closing_conditions"] == []


def test_posts_take_no_overlapping_records(owner: Client, s: Scenario) -> None:
    oil = _work(owner, s)
    created = post(
        owner, f"/api/c/{s.cmp}/service/posts", {"location_id": s.location, "name": "Lift 1"}, key()
    )
    assert created.status_code == 201, created.content
    lift = created.json()["post"]["id"]
    _op(owner, s, [oil["id"]], planned_start=_at(10), planned_end=_at(12), post_id=lift)
    second = add_vehicle(owner, s, s.location, name="Honda Click")
    overlap = _operation(
        owner,
        s,
        [oil["id"]],
        vehicle_card_id=second,
        planned_start=_at(11),
        planned_end=_at(13),
        post_id=lift,
    )
    assert _code(overlap) == "POST_NOT_AVAILABLE"
    # Without a post the general zone has no limit (§ 16.3.4).
    zone = _op(
        owner, s, [oil["id"]], vehicle_card_id=second, planned_start=_at(11), planned_end=_at(13)
    )
    assert zone["post_id"] is None


def test_service_holds_the_vehicle(owner: Client, s: Scenario) -> None:
    oil = _work(owner, s)
    op = _op(owner, s, [oil["id"]], planned_start=_at(24), planned_end=_at(30))
    booking = post(
        owner,
        f"/api/c/{s.cmp}/bookings",
        {"vehicle_card_id": s.vehicle, "planned_start": _at(20), "planned_end": _at(26)},
        key(),
    )
    assert _code(booking) == "VEHICLE_NOT_AVAILABLE"
    assert booking.json()["error"]["params"]["conflicts"][0]["kind"] == "service"

    op = _ok(_do(owner, s, op, "plan", planned_start=_at(60), planned_end=_at(70)))
    op = _ok(_do(owner, s, op, "accept", accepted_at=_at(-1)))
    issued = issue(owner, s, actual_start=NOW.isoformat())
    assert _code(issued) == "VEHICLE_NOT_AVAILABLE"

    # A final state frees the vehicle (§ 16.6.7).
    _ok(_do(owner, s, op, "cancel", reason="Client changed plans"))
    assert issue(owner, s, actual_start=NOW.isoformat()).status_code == 201


def test_own_vehicle_is_accepted_in_its_location(owner: Client, s: Scenario) -> None:
    oil = _work(owner, s)
    other = add_location(owner, s.cmp, "Patong")
    post(owner, f"/api/c/{s.cmp}/locations/{other}/contexts", {"type": "service"}, key())
    op = _op(owner, s, [oil["id"]], location_id=other)
    # § 16.2.1: another location needs an internal move first.
    assert _code(_do(owner, s, op, "accept", accepted_at=_at(-1))) == "VEHICLE_LOCATION_MISMATCH"


def test_terminate_and_cancel(owner: Client, s: Scenario) -> None:
    oil = _work(owner, s)
    op = _op(owner, s, [oil["id"], oil["id"]])
    op = _ok(_do(owner, s, op, "accept", accepted_at=_at(-3)))
    op = _ok(_job(owner, s, op["jobs"][0], "start", actual_start=_at(-2)))
    assert _code(_do(owner, s, op, "cancel", reason="x")) == "SERVICE_OPERATION_STARTED"
    op = _ok(_do(owner, s, op, "terminate", reason="Parts missing"))
    assert op["state"] == "terminated"
    assert [j["state"] for j in op["jobs"]] == ["terminated", "cancelled"]
    order = _order(owner, s, op["order_id"])
    assert [c["condition"] for c in order["closing_conditions"]] == ["service_price_missing"]


def test_terminated_work_takes_the_given_price(owner: Client, s: Scenario) -> None:
    oil = _work(owner, s)
    op = _op(owner, s, [oil["id"]])
    op = _ok(_do(owner, s, op, "accept", accepted_at=_at(-3)))
    op = _ok(_job(owner, s, op["jobs"][0], "start", actual_start=_at(-2)))
    no_reason = _job(owner, s, op["jobs"][0], "terminate", actual_end=_at(-1))
    assert no_reason.status_code == 400
    op = _ok(
        _job(owner, s, op["jobs"][0], "terminate", actual_end=_at(-1), reason="Broken", price="200")
    )
    assert _order(owner, s, op["order_id"])["amounts"]["THB"]["charged"] == "200.00"


def test_order_cancel_takes_its_operations(owner: Client, s: Scenario) -> None:
    oil = _work(owner, s)
    op = _op(owner, s, [oil["id"]])
    order = _order(owner, s, op["order_id"])
    cancelled = post(
        owner,
        f"/api/c/{s.cmp}/orders/{order['id']}/cancel",
        {"expected_version": order["version"], "reason": "No show"},
        key(),
    )
    assert cancelled.status_code == 200, cancelled.content
    op = owner.get(f"/api/c/{s.cmp}/service/operations/{op['id']}").json()["operation"]
    assert op["state"] == "cancelled"

    started = _op(owner, s, [oil["id"]])
    started = _ok(_do(owner, s, started, "accept", accepted_at=_at(-3)))
    _ok(_job(owner, s, started["jobs"][0], "start", actual_start=_at(-2)))
    order = _order(owner, s, started["order_id"])
    refused = post(
        owner,
        f"/api/c/{s.cmp}/orders/{order['id']}/cancel",
        {"expected_version": order["version"], "reason": "No show"},
        key(),
    )
    assert _code(refused) == "ORDER_HAS_STARTED_OPERATIONS"


def test_mechanic_takes_free_work(owner: Client, s: Scenario) -> None:
    oil = _work(owner, s)
    mechanic, employment = hire(
        owner, s.cmp, "mechanic@example.com", [{"type": "set", "set": "mechanic"}]
    )
    other, other_employment = hire(
        owner, s.cmp, "other@example.com", [{"type": "set", "set": "mechanic"}]
    )
    op = _op(owner, s, [oil["id"], oil["id"]])
    op = _ok(_do(owner, s, op, "accept", accepted_at=_at(-3)))

    queue = mechanic.get(f"/api/c/{s.cmp}/service/queue").json()["jobs"]
    assert {j["id"] for j in queue} == {j["id"] for j in op["jobs"]}
    op = _ok(_job(mechanic, s, op["jobs"][0], "take"))
    assert op["jobs"][0]["executor_id"] == employment
    # A work of another mechanic is not taken or started without reassignment (§ 23.2.4).
    assert _code(_job(other, s, op["jobs"][0], "take")) == "SERVICE_JOB_ASSIGNED"
    assert _job(other, s, op["jobs"][0], "start", actual_start=_at(-2)).status_code == 403

    op = _ok(_job(mechanic, s, op["jobs"][0], "start", actual_start=_at(-2)))
    op = _ok(_job(mechanic, s, op["jobs"][0], "complete", actual_end=_at(-1), odometer="10500"))
    assert op["jobs"][0]["state"] == "done"
    # No prices and no operation-level actions for a mechanic.
    assert _job(mechanic, s, op["jobs"][1], "price", price="1").status_code == 403
    assert _do(mechanic, s, op, "finish").status_code == 403
    assert other_employment != employment
