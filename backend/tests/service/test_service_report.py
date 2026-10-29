"""Reports, part О5: the dashboard «Service» (ТЗ § 21.7.1; decision О-7)."""

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
from tests.service.test_service import _at, _do, _done, _ok, _op, _work

pytestmark = pytest.mark.django_db

PERIOD = "start=2026-11-03&end=2026-11-03"


@pytest.fixture(autouse=True)
def report_clock() -> Iterator[None]:
    with mock.patch.object(
        service_report, "timezone", SimpleNamespace(now=lambda: NOW + timedelta(hours=1))
    ):
        yield


def _blocks(client: Client, s: Scenario, query: str = PERIOD) -> dict[str, dict[str, Any]]:
    response = client.get(f"/api/c/{s.cmp}/reports/service?{query}")
    assert response.status_code == 200, response.content
    return {b["code"]: b for b in response.json()["blocks"]}


def test_service_dashboard(owner: Client, s: Scenario) -> None:
    post_id = post(
        owner, f"/api/c/{s.cmp}/service/posts", {"name": "Lift 1", "location_id": s.location}, key()
    ).json()["post"]["id"]
    op = _op(owner, s, [_work(owner, s)["id"]])
    op = _ok(_do(owner, s, op, "accept", accepted_at=_at(-3)))
    op = _ok(_do(owner, s, op, "place", post_id=post_id))
    blocks = _blocks(owner, s)
    assert blocks["service.on_posts"]["value"] == 1 and blocks["service.general_zone"]["value"] == 0
    lift = blocks["service.posts"]["rows"][0]
    assert lift["busy"] is True
    op = _done(owner, s, op)
    op = _ok(_do(owner, s, op, "finish"))
    blocks = _blocks(owner, s)
    assert blocks["service.finished"]["value"] == 1
    # Accepted 07:00, finished 10:00 (Bangkok): 3 h of actual duration.
    assert blocks["service.actual_hours_avg"]["value"] == "3.0"
    lift = blocks["service.posts"]["rows"][0]
    # The day is open 24 h by default: occupied 3 h of 11 h up to the moment of the report.
    assert lift["busy"] is False and lift["occupied_hours"] == "3.0"
    assert lift["working_hours"] == "11.0" and lift["utilization"] == "27.3"
    assert [r["works"] for r in blocks["service.by_executor"]["rows"]] == [1]


def test_warehouse_and_service_hidden_without_rights(owner: Client, s: Scenario) -> None:
    from tests.companies.helpers import hire

    reader, _ = hire(
        owner, s.cmp, "r@example.com", [{"type": "grant", "permission": "reports.view"}]
    )
    assert _blocks(reader, s) == {}
    assert reader.get(f"/api/c/{s.cmp}/reports/warehouse").json()["blocks"] == []
    exported = post(owner, f"/api/c/{s.cmp}/reports/service/xlsx?{PERIOD}", {"labels": {}}, key())
    assert exported.status_code == 200
