"""Agreement of additions with the client (ТЗ § 12.3; decisions З9, З10, В-1; part А6.2)."""

from datetime import timedelta
from typing import Any

import pytest
from django.test import Client
from django.utils import timezone

from c4rsys.orders.models import OrderApproval
from tests.accounts.helpers import post
from tests.companies.helpers import key, named_user
from tests.rentals.scenario import Scenario
from tests.service.test_service import _at, _do, _job, _ok, _op, _work

pytestmark = pytest.mark.django_db


def _policy(owner: Client, s: Scenario, value: bool) -> None:
    response = owner.put(
        f"/api/c/{s.cmp}/order-policy",
        data={"require_approval": value},
        content_type="application/json",
        headers=key(),
    )
    assert response.status_code == 200, response.content


def _approvals(owner: Client, s: Scenario, order_id: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = owner.get(f"/api/c/{s.cmp}/orders/{order_id}/approvals").json()[
        "approvals"
    ]
    return found


def _in_work(owner: Client, s: Scenario) -> dict[str, Any]:
    oil = _work(owner, s, name="Oil")
    op = _op(owner, s, [oil["id"]])
    op = _ok(_do(owner, s, op, "accept", accepted_at=_at(-3)))
    return _ok(_job(owner, s, op["jobs"][0], "start", actual_start=_at(-2)))


def test_off_by_default_and_before_the_work(owner: Client, s: Scenario) -> None:
    assert owner.get(f"/api/c/{s.cmp}/order-policy").json() == {"require_approval": False}
    op = _in_work(owner, s)
    brakes = _work(owner, s, name="Brakes")
    _ok(_do(owner, s, op, "jobs", work_id=brakes["id"]))
    assert not OrderApproval.objects.exists()

    # An order not in work yet: the position is part of what the client asked for.
    _policy(owner, s, True)
    fresh = _op(owner, s, [brakes["id"]])
    _ok(_do(owner, s, fresh, "jobs", work_id=brakes["id"]))
    assert not OrderApproval.objects.exists()


def test_waiting_position_proceed_and_refusal(owner: Client, s: Scenario) -> None:
    _policy(owner, s, True)
    op = _in_work(owner, s)
    order_id = op["order_id"]
    brakes = _work(owner, s, name="Brakes")
    op = _ok(_do(owner, s, op, "jobs", work_id=brakes["id"]))
    pending = _approvals(owner, s, order_id)
    assert [(a["state"], a["title"], a["item_type"]) for a in pending] == [
        ("pending", "Brakes", "service_job")
    ]
    # § 12.3.3: it waits; § 12.3.2: «Do without the client's confirmation» lets it start.
    second = op["jobs"][1]
    blocked = _job(owner, s, second, "start", actual_start=_at(-1))
    assert blocked.json()["error"]["code"] == "APPROVAL_PENDING"
    order = owner.get(f"/api/c/{s.cmp}/orders/{order_id}").json()["order"]
    assert any(c["condition"] == "awaiting_client" for c in order["closing_conditions"])
    url = f"/api/c/{s.cmp}/orders/{order_id}/approvals/{pending[0]['id']}"
    assert post(owner, f"{url}/proceed", {}, key()).status_code == 200
    op = _ok(_job(owner, s, second, "start", actual_start=_at(-1)))
    # No answer is not a refusal; proceeding is not the client's consent.
    assert _approvals(owner, s, order_id)[0]["state"] == "pending"

    tyres = _work(owner, s, name="Tyres")
    op = _ok(_do(owner, s, op, "jobs", work_id=tyres["id"]))
    tyre = next(a for a in _approvals(owner, s, order_id) if a["title"] == "Tyres")
    answer = {
        "decision": "declined",
        "method": "phone",
        "comment": "too expensive",
        "answered_at": (timezone.now() - timedelta(minutes=5)).isoformat(),
    }
    declined = post(
        owner, f"/api/c/{s.cmp}/orders/{order_id}/approvals/{tyre['id']}/answer", answer, key()
    )
    assert declined.status_code == 200, declined.content
    # § 12.3.1: a refused position not started is cancelled; the rest goes on.
    jobs = owner.get(f"/api/c/{s.cmp}/service/operations/{op['id']}").json()["operation"]["jobs"]
    assert [j["state"] for j in jobs] == ["in_progress", "in_progress", "cancelled"]
    stored = OrderApproval.objects.get(pk=tyre["id"])
    assert stored.answer_source == "employee" and stored.contact_method == "phone"


def test_the_client_answers_in_the_cabinet(owner: Client, s: Scenario) -> None:
    _policy(owner, s, True)
    op = _in_work(owner, s)
    brakes = _work(owner, s, name="Brakes")
    _ok(_do(owner, s, op, "jobs", work_id=brakes["id"]))

    ivan = Client()
    named_user(ivan, "ivan@example.com")
    usr = ivan.get("/api/me").json()["user"]["public_id"]
    body = {"card_type": "client", "card_id": s.client, "target": usr}
    link = post(owner, f"/api/c/{s.cmp}/profile-links", body, key()).json()["link"]
    post(ivan, f"/api/profile-links/{link['id']}/accept", {"fields": []}, key())

    company = ivan.get("/api/cabinet/companies").json()["companies"][0]
    orders = ivan.get(f"/api/cabinet/companies/{company['link_id']}/history").json()["orders"]
    waiting = orders[0]["approvals"]
    assert [(a["title"], a["state"]) for a in waiting] == [("Brakes", "pending")]
    answered = post(
        ivan, f"/api/cabinet/approvals/{waiting[0]['id']}/answer", {"decision": "approved"}, key()
    )
    assert answered.status_code == 200, answered.content
    stored = OrderApproval.objects.get(pk=waiting[0]["id"])
    assert stored.state == "approved" and stored.answer_source == "client"
    # A stranger cannot answer.
    other = Client()
    named_user(other, "other@example.com")
    again = post(
        other, f"/api/cabinet/approvals/{waiting[0]['id']}/answer", {"decision": "declined"}, key()
    )
    assert again.status_code == 404


def test_cost_change_is_a_notice(owner: Client, s: Scenario) -> None:
    """Decision В-1: a change of the price is not agreed; the client sees it in the order."""
    _policy(owner, s, True)
    op = _in_work(owner, s)
    op = _ok(_job(owner, s, op["jobs"][0], "complete", actual_end=_at(-1), odometer="10500"))
    changed = _job(owner, s, op["jobs"][0], "price", price="900")
    assert changed.status_code == 200, changed.content
    assert not OrderApproval.objects.exists()
    from c4rsys.orders import services
    from c4rsys.orders.models import Order

    view = services.client_view(Order.objects.get(pk=op["order_id"]))
    assert [c["reason"] for c in view["cost_changes"]] == ["service_job_price_changed"]
