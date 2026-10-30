"""The schedule of vehicles (decisions Б3, В-4; part А8)."""

from datetime import timedelta
from urllib.parse import quote

import pytest
from django.test import Client

from tests.companies.helpers import hire
from tests.rentals.scenario import NOW, Scenario, issue
from tests.service.test_service import _at, _do, _ok, _op, _work

pytestmark = pytest.mark.django_db


def _schedule(client: Client, s: Scenario, **params: str) -> dict[str, object]:
    start = quote((NOW - timedelta(days=1)).isoformat())
    end = quote((NOW + timedelta(days=6)).isoformat())
    extra = "".join(f"&{k}={v}" for k, v in params.items())
    response = client.get(f"/api/c/{s.cmp}/vehicles/schedule?from={start}&to={end}{extra}")
    assert response.status_code == 200, response.content
    found: dict[str, object] = response.json()
    return found


def test_rental_and_service_on_the_schedule(owner: Client, s: Scenario) -> None:
    rented = issue(owner, s, actual_start=_at(-5))
    assert rented.status_code == 201, rented.content
    oil = _work(owner, s, name="Oil")
    op = _op(owner, s, [oil["id"]], planned_start=_at(30), planned_end=_at(32))
    assert op["state"] == "planned"

    found = _schedule(owner, s)
    vehicles = found["vehicles"]
    assert isinstance(vehicles, list) and [v["id"] for v in vehicles] == [s.vehicle]
    items = found["items"]
    assert isinstance(items, list)
    kinds = sorted((i["kind"], i["planned"]) for i in items)
    assert kinds == [("rental", False), ("service", True)]
    assert _schedule(owner, s, location=s.location)["items"] == items

    # Each kind only with its own right: a mechanic sees the service, not the rental.
    rights = [{"type": "grant", "permission": p} for p in ("vehicle.view", "service.view")]
    mechanic, _ = hire(owner, s.cmp, "mechanic@example.com", rights)
    seen = _schedule(mechanic, s)["items"]
    assert isinstance(seen, list) and [i["kind"] for i in seen] == ["service"]
    _ok(_do(owner, s, op, "cancel", reason="not needed"))
    left = _schedule(owner, s)["items"]
    assert isinstance(left, list) and [i["kind"] for i in left] == ["rental"]
