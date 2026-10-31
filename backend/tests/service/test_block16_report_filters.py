"""Block 16 Б: filters of the dashboard «Service» by post, work of the system reference, executor
and order (ТЗ § 21.7.1)."""

from collections.abc import Iterator
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from django.test import Client

from c4rsys.reports import service as service_report
from tests.accounts.helpers import post
from tests.companies.helpers import key
from tests.rentals.scenario import NOW, Scenario
from tests.service.test_service import _at, _do, _done, _ok, _op, _order, _work

pytestmark = pytest.mark.django_db

# Wide: the records keep the real moment of creation.
PERIOD = "start=2026-01-01&end=2027-12-31"


@pytest.fixture(autouse=True)
def report_clock() -> Iterator[None]:
    with mock.patch.object(
        service_report, "timezone", SimpleNamespace(now=lambda: NOW + timedelta(hours=1))
    ):
        yield


def _blocks(client: Client, s: Scenario, extra: str = "") -> dict[str, dict[str, Any]]:
    response = client.get(f"/api/c/{s.cmp}/reports/service?{PERIOD}&{extra}")
    assert response.status_code == 200, response.content
    return {b["code"]: b for b in response.json()["blocks"]}


def test_service_filters(owner: Client, s: Scenario) -> None:
    lift = post(
        owner, f"/api/c/{s.cmp}/service/posts", {"name": "Lift 1", "location_id": s.location}, key()
    ).json()["post"]["id"]
    other = post(
        owner, f"/api/c/{s.cmp}/service/posts", {"name": "Lift 2", "location_id": s.location}, key()
    ).json()["post"]["id"]
    oil = _work(owner, s, system_work="engine_oil", name="Oil")
    op = _op(owner, s, [oil["id"]])
    op = _ok(_do(owner, s, op, "accept", accepted_at=_at(-3)))
    op = _ok(_do(owner, s, op, "place", post_id=lift))
    op = _done(owner, s, op)
    executor = op["jobs"][0]["executor_id"]
    number = _order(owner, s, op["order_id"])["number"]

    def jobs(extra: str) -> int:
        rows = _blocks(owner, s, extra)["service.jobs_by_state"]["rows"]
        return sum(int(r["count"]) for r in rows)

    assert jobs("") == 1
    assert jobs(f"post={lift}") == 1 and jobs(f"post={other}") == 0
    posts = _blocks(owner, s, f"post={lift}")["service.posts"]["rows"]
    assert [p["post"] for p in posts] == ["Phuket · Lift 1"]
    assert jobs("work=engine_oil") == 1 and jobs("work=brake_pads") == 0
    assert jobs(f"order={number}") == 1 and jobs(f"order={number + 1}") == 0
    if executor:
        assert jobs(f"executor={executor}") == 1
    assert jobs("executor=0190a3e0-0000-7000-8000-000000000000") == 0
    assert owner.get(f"/api/c/{s.cmp}/reports/service?order=abc").status_code == 400
