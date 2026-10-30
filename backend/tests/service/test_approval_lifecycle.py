"""Requests to the client when the position goes away, the order is not open or parts are
issued; a reopened service and its occupancy; a correction of the comment alone (ТЗ § 12.3,
§ 16.8.2; parts А6.2, С6)."""

from datetime import timedelta
from typing import Any

import pytest
from django.test import Client
from django.utils import timezone

from c4rsys.fleet.models import OdometerReading
from c4rsys.orders.models import Order, OrderApproval
from c4rsys.service import operations
from c4rsys.service.models import ServiceOperation
from tests.accounts.helpers import post
from tests.companies.helpers import key
from tests.rentals.scenario import NOW, Scenario, add_vehicle, issue
from tests.service.test_approvals import _approvals, _in_work, _policy
from tests.service.test_maintenance import _service_done
from tests.service.test_materials import _add, _asset, stock  # noqa: F401
from tests.service.test_service import _at, _code, _do, _job, _ok, _op, _order, _work

pytestmark = pytest.mark.django_db


def _waiting(owner: Client, s: Scenario) -> tuple[dict[str, Any], dict[str, Any]]:
    """An order in work with a second work that waits for the client."""
    _policy(owner, s, True)
    op = _in_work(owner, s)
    brakes = _work(owner, s, name="Brakes")
    op = _ok(_do(owner, s, op, "jobs", work_id=brakes["id"]))
    return op, _approvals(owner, s, op["order_id"])[0]


def _answer(owner: Client, s: Scenario, order_id: str, approval_id: str, decision: str) -> Any:
    body = {
        "decision": decision,
        "method": "phone",
        "comment": "",
        "answered_at": (timezone.now() - timedelta(minutes=5)).isoformat(),
    }
    url = f"/api/c/{s.cmp}/orders/{order_id}/approvals/{approval_id}/answer"
    return post(owner, url, body, key())


def test_cancelled_work_withdraws_its_request(owner: Client, s: Scenario) -> None:
    op, approval = _waiting(owner, s)
    assert approval["state"] == "pending"
    _ok(_job(owner, s, op["jobs"][1], "cancel", reason="not needed"))
    # The request has nothing to agree: it does not keep the order from closing.
    assert _approvals(owner, s, op["order_id"])[0]["state"] == "withdrawn"
    conditions = [c["condition"] for c in _order(owner, s, op["order_id"])["closing_conditions"]]
    assert "awaiting_client" not in conditions
    late = _answer(owner, s, op["order_id"], approval["id"], "approved")
    assert _code(late) == "APPROVAL_WITHDRAWN"


def test_no_answer_while_the_order_is_not_open(owner: Client, s: Scenario) -> None:
    op, approval = _waiting(owner, s)
    Order.objects.filter(pk=op["order_id"]).update(
        state=Order.State.CANCELLED, cancelled_at=timezone.now()
    )
    refused = _answer(owner, s, op["order_id"], approval["id"], "declined")
    assert _code(refused) == "ORDER_CLOSED"
    url = f"/api/c/{s.cmp}/orders/{op['order_id']}/approvals/{approval['id']}/proceed"
    assert _code(post(owner, url, {}, key())) == "ORDER_CLOSED"
    assert OrderApproval.objects.get(pk=approval["id"]).state == "pending"


def test_refusal_of_a_work_with_issued_parts_is_recorded(
    owner: Client,
    s: Scenario,
    stock: dict[str, Any],  # noqa: F811
) -> None:
    op, approval = _waiting(owner, s)
    added = _add(
        owner, s, op["jobs"][1], stock, item_id=stock["filter"], asset_ids=[_asset(stock["filter"])]
    )
    assert added.status_code == 201, added.content
    op = added.json()["operation"]
    # The refusal is not rolled back: the work stays planned until the part goes back.
    declined = _answer(owner, s, op["order_id"], approval["id"], "declined")
    assert declined.status_code == 200, declined.content
    assert OrderApproval.objects.get(pk=approval["id"]).state == "declined"
    op = owner.get(f"/api/c/{s.cmp}/service/operations/{op['id']}").json()["operation"]
    assert op["jobs"][1]["state"] == "planned"
    blocked = _job(owner, s, op["jobs"][1], "start", actual_start=_at(-1))
    assert _code(blocked) == "APPROVAL_DECLINED"


def test_reopened_service_takes_no_new_occupancy(owner: Client, s: Scenario) -> None:
    oil = _work(owner, s, system_work="engine_oil")
    op = _service_done(owner, s, oil, "10500", end=-3)
    finished_at = ServiceOperation.objects.get(pk=op["id"]).finished_at
    assert finished_at is not None
    reopened = _ok(_do(owner, s, op, "reopen", reason="one more check"))
    assert reopened["state"] == "in_progress"
    stored = ServiceOperation.objects.get(pk=op["id"])
    # § 16.8.2: the historical interval stays; nothing is claimed from the opening on.
    assert stored.finished_at == finished_at
    assert operations.vehicle_busy(stored.company_id, stored.vehicle_card_id) == []
    later = operations.occupancy(stored.company_id, stored.vehicle_card_id, finished_at, None)
    assert later == []
    earlier = operations.occupancy(
        stored.company_id, stored.vehicle_card_id, finished_at - timedelta(hours=1), None
    )
    assert [o["id"] for o in earlier] == [op["id"]]
    # The vehicle goes to a rental after the finish while the record is open.
    issued = issue(owner, s, actual_start=NOW.isoformat(), odometer="10600")
    assert issued.status_code == 201, issued.content
    done = _ok(_do(owner, s, reopened, "finish"))
    assert done["state"] == "works_done"
    assert ServiceOperation.objects.get(pk=op["id"]).finished_at == finished_at


def test_reopen_refused_when_the_post_is_planned_for_another(owner: Client, s: Scenario) -> None:
    created = post(
        owner, f"/api/c/{s.cmp}/service/posts", {"location_id": s.location, "name": "Lift 1"}, key()
    )
    assert created.status_code == 201, created.content
    lift = created.json()["post"]["id"]
    oil = _work(owner, s)
    op = _op(owner, s, [oil["id"]], planned_start=_at(-6), planned_end=_at(10), post_id=lift)
    op = _ok(_do(owner, s, op, "accept", accepted_at=_at(-5), post_id=lift))
    op = _ok(_job(owner, s, op["jobs"][0], "start", actual_start=_at(-4)))
    op = _ok(_job(owner, s, op["jobs"][0], "complete", actual_end=_at(-3), odometer="10500"))
    op = _ok(_do(owner, s, op, "finish"))
    # After the finish the post is planned for another vehicle within the old plan.
    second = add_vehicle(owner, s, s.location, name="Honda Click")
    _op(
        owner,
        s,
        [oil["id"]],
        vehicle_card_id=second,
        planned_start=_at(2),
        planned_end=_at(4),
        post_id=lift,
    )
    refused = _do(owner, s, op, "reopen", reason="check")
    assert _code(refused) == "SERVICE_REOPEN_POST_TAKEN"
    assert ServiceOperation.objects.get(pk=op["id"]).state == "works_done"


def test_correction_of_the_comment_keeps_times_and_odometer(owner: Client, s: Scenario) -> None:
    oil = _work(owner, s, system_work="engine_oil")
    op = _op(owner, s, [oil["id"]])
    op = _ok(_do(owner, s, op, "accept", accepted_at=_at(-3)))
    op = _ok(_job(owner, s, op["jobs"][0], "start", actual_start=_at(-2)))
    end = NOW - timedelta(minutes=50, seconds=23)
    op = _ok(
        _job(owner, s, op["jobs"][0], "complete", actual_end=end.isoformat(), odometer="10500")
    )
    op = _ok(_do(owner, s, op, "finish"))
    stored = op["jobs"][0]
    job = _ok(_job(owner, s, stored, "open", reason="comment"))["jobs"][0]
    # A form that shows the minute sends the same minute back: it is not a change.
    body = {
        "expected_version": job["version"],
        "reason": "typo",
        "comment": "checked again",
        "actual_end": end.replace(second=0).isoformat(),
    }
    fixed = post(owner, f"/api/c/{s.cmp}/service/jobs/{job['id']}/correct", body, key())
    assert fixed.status_code == 200, fixed.content
    corrected = fixed.json()["operation"]["jobs"][0]
    assert corrected["actual_end"] == stored["actual_end"]
    assert corrected["comment"] == "checked again"
    readings = OdometerReading.objects.filter(source_type="service_job", source_id=job["id"])
    assert readings.count() == 1
