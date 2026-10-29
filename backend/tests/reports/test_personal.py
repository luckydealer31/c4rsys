"""Reports, part О6: personal dashboards (ТЗ § 21.2.4–21.2.5; decision О-8)."""

from typing import Any

import pytest
from django.test import Client

from tests.accounts.helpers import post
from tests.companies.helpers import hire, key
from tests.orders.test_close import frozen_clock  # noqa: F401
from tests.rentals.scenario import Scenario, issue

pytestmark = pytest.mark.django_db


def _create(client: Client, s: Scenario, name: str, metrics: list[str]) -> Any:
    return post(
        client, f"/api/c/{s.cmp}/reports/personal", {"name": name, "metrics": metrics}, key()
    )


def test_own_dashboard_of_chosen_metrics(owner: Client, s: Scenario) -> None:
    issue(owner, s)
    catalog = owner.get(f"/api/c/{s.cmp}/reports/personal").json()["catalog"]
    assert {
        "id": "finance:finance.result",
        "dashboard": "finance",
        "code": "finance.result",
    } in catalog
    metrics = ["rental:fleet.utilization", "overview:rental.active", "service:service.overdue"]
    created = _create(owner, s, "My morning", metrics)
    assert created.status_code == 201, created.content
    mine = created.json()["dashboard"]
    blocks = owner.get(f"/api/c/{s.cmp}/reports/p-{mine['id']}").json()["blocks"]
    # The chosen order; a metric of a section without the context of service still has its block.
    assert [b["code"] for b in blocks][:2] == ["fleet.utilization", "rental.active"]
    assert blocks[1]["value"] == 1
    unknown = _create(owner, s, "Bad", ["finance:finance.profit"])
    assert unknown.json()["error"]["code"] == "REPORT_METRIC_UNKNOWN"
    changed = owner.put(
        f"/api/c/{s.cmp}/reports/personal/{mine['id']}",
        data={"name": "Morning", "metrics": ["overview:rental.active"], "expected_version": 1},
        content_type="application/json",
        headers=key(),
    )
    assert changed.json()["dashboard"]["metrics"] == ["overview:rental.active"]


def test_personal_is_not_shared(owner: Client, s: Scenario) -> None:
    mine = _create(owner, s, "Mine", ["overview:rental.active"]).json()["dashboard"]
    other, _ = hire(
        owner,
        s.cmp,
        "o@example.com",
        [{"type": "grant", "permission": "reports.view"}],
    )
    # § 21.2.4: another employee neither sees nor opens it.
    assert other.get(f"/api/c/{s.cmp}/reports/personal").json()["dashboards"] == []
    assert other.get(f"/api/c/{s.cmp}/reports/p-{mine['id']}").status_code == 404
    removed = owner.delete(f"/api/c/{s.cmp}/reports/personal/{mine['id']}", headers=key())
    assert removed.status_code == 200
    assert owner.get(f"/api/c/{s.cmp}/reports/personal").json()["dashboards"] == []
