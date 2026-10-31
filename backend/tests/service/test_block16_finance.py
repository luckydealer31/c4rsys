"""The finance result by locations and vehicles (ТЗ § 17.2.4, § 17.3.1–17.3.2, § 16.6.5;
block 16)."""

from datetime import timedelta
from typing import Any

import pytest
from django.test import Client
from django.utils import timezone

from tests.finance.test_settlements import _account
from tests.rentals.scenario import NOW, Scenario
from tests.service.test_external import garage  # noqa: F401
from tests.service.test_service import _at, _do, _job, _ok, _op, _work

pytestmark = pytest.mark.django_db

# The works are dated by the frozen clock of the service, the charges by the real one.
_days = sorted([NOW.date(), timezone.localdate()])
PERIOD = f"start={_days[0] - timedelta(days=10)}&end={_days[1] + timedelta(days=1)}"


def _blocks(client: Client, s: Scenario, query: str = "") -> dict[str, dict[str, Any]]:
    response = client.get(f"/api/c/{s.cmp}/reports/finance?{PERIOD}&{query}")
    assert response.status_code == 200, response.content
    return {b["code"]: b for b in response.json()["blocks"]}


def test_contractor_work_is_income_and_expense_of_the_location(
    owner: Client,
    s: Scenario,
    garage: str,  # noqa: F811
) -> None:
    _account(owner, s.cmp, "Desk")
    gearbox = _work(owner, s, name="Gearbox repair", price="8000")
    op = _op(owner, s, [gearbox["id"]], location_id=None, counterparty_id=garage)
    op = _ok(_do(owner, s, op, "handovers", kind="vehicle", handed_at=_at(-5)))
    op = _ok(_job(owner, s, op["jobs"][0], "start", actual_start=_at(-4)))
    op = _ok(_job(owner, s, op["jobs"][0], "complete", actual_end=_at(-2), contractor_cost="5000"))

    blocks = _blocks(owner, s)
    # § 17.3.1: the income charged to the client and the contractor's cost belong to the location
    # of the vehicle (the operation is at a counterparty); one expense, not multiplied.
    rows = blocks["finance.result_by_location"]["rows"]
    assert rows == [
        {
            "name": "Phuket",
            "currency": "THB",
            "income": "8000.00",
            "expenses": "5000.00",
            "result": "3000.00",
        }
    ]
    assert blocks["finance.result_by_vehicle"]["rows"][0]["name"] == "Toyota Yaris"
    assert blocks["finance.result_by_vehicle"]["rows"][0]["result"] == "3000.00"
    # § 17.3.2: the analytic dimensions of the expense.
    cost = blocks["finance.service_costs"]["rows"]
    assert len(cost) == 1
    assert cost[0]["order"] is not None and cost[0]["incomplete"] is False
    assert (cost[0]["vehicle"], cost[0]["location"], cost[0]["execution"]) == (
        "Toyota Yaris",
        "Phuket",
        "—",
    )
    assert (cost[0]["expense_kind"], cost[0]["amount"]) == ("contractor", "5000.00")
    # The location filter keeps only what belongs to the location.
    other = _blocks(owner, s, f"location={s.location}")["finance.result_by_location"]["rows"]
    assert [r["name"] for r in other] == ["Phuket"]


def test_post_is_freed_when_every_work_has_its_result(owner: Client, s: Scenario) -> None:
    # § 16.3.5 (block 16): the post is freed automatically, the operation goes on.
    from c4rsys.platform.models import AuditEvent
    from tests.accounts.helpers import post
    from tests.companies.helpers import key

    oil = _work(owner, s)
    lift = post(
        owner, f"/api/c/{s.cmp}/service/posts", {"location_id": s.location, "name": "Lift 1"}, key()
    ).json()["post"]["id"]
    op = _op(owner, s, [oil["id"], oil["id"]], post_id=lift)
    op = _ok(_do(owner, s, op, "accept", accepted_at=_at(-5), post_id=lift))
    assert op["post_id"] == lift
    op = _ok(_job(owner, s, op["jobs"][0], "start", actual_start=_at(-4)))
    op = _ok(_job(owner, s, op["jobs"][0], "complete", actual_end=_at(-3), odometer="10100"))
    # One work is still planned: the post stays taken.
    assert op["post_id"] == lift and op["post_released_at"] is None
    op = _ok(_job(owner, s, op["jobs"][1], "cancel", reason="Not needed"))
    assert op["post_released_at"] is not None and op["state"] in ("accepted", "in_progress")
    entry = AuditEvent.objects.get(action="service_operation.post_released")
    assert entry.after is not None and entry.after["automatic"] is True
    # The post takes another vehicle; the first one stays in the service.
    from tests.rentals.scenario import add_vehicle

    other = _op(owner, s, [oil["id"]], vehicle_card_id=add_vehicle(owner, s, s.location, "Click"))
    other = _ok(_do(owner, s, other, "accept", accepted_at=_at(-2), post_id=lift))
    assert other["post_id"] == lift


def test_trip_to_the_contractor_hands_the_vehicle_over(
    owner: Client,
    s: Scenario,
    garage: str,  # noqa: F811
) -> None:
    # § 16.6.6 (block 16): finishing the trip records «vehicle handed to the contractor».
    from types import SimpleNamespace
    from unittest import mock

    from c4rsys.delivery import services as delivery_module
    from tests.delivery.test_deliveries import _create
    from tests.delivery.test_deliveries import _do as trip_do
    from tests.delivery.test_deliveries import _ok as trip_ok

    gearbox = _work(owner, s, name="Gearbox repair", price="8000")
    op = _op(owner, s, [gearbox["id"]], location_id=None, counterparty_id=garage)
    with mock.patch.object(delivery_module, "timezone", SimpleNamespace(now=lambda: NOW)):
        driver = owner.get(f"/api/c/{s.cmp}/deliveries/drivers").json()["drivers"][0]["id"]
        to = {"kind": "other", "address": "Patong Gearbox"}
        d = trip_ok(_create(owner, s, to, order_id=op["order_id"], driver_id=driver), 201)
        d = trip_ok(trip_do(owner, s, d, "start", actual_start=_at(-5)))
        d = trip_ok(
            trip_do(owner, s, d, "finish", actual_end=_at(-4), result="other", comment="garage")
        )
    found = owner.get(f"/api/c/{s.cmp}/service/operations/{op['id']}").json()["operation"]
    assert [h["kind"] for h in found["handovers"]] == ["vehicle"]
    assert found["handovers"][0]["returned_at"] is None
    assert found["state"] == "accepted"
