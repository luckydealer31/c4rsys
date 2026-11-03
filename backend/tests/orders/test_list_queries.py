"""Audit item 14: the list of orders reads the database a fixed number of times, not once more
for every order."""

from datetime import timedelta

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from tests.accounts.helpers import post
from tests.companies.helpers import key
from tests.rentals.scenario import Scenario, build

pytestmark = pytest.mark.django_db


@pytest.fixture
def s(owner: Client) -> Scenario:
    return build(owner)


def _book(owner: Client, s: Scenario, vehicle: str, day: int) -> None:
    start = timezone.now().replace(microsecond=0) + timedelta(days=day)
    body = {
        "vehicle_card_id": vehicle,
        "planned_start": start.isoformat(),
        "planned_end": (start + timedelta(days=1)).isoformat(),
        "tariff_id": s.tariff,
    }
    response = post(owner, f"/api/c/{s.cmp}/bookings", body, key())
    assert response.status_code == 201, response.content


def _list_queries(owner: Client, s: Scenario) -> tuple[int, int]:
    with CaptureQueriesContext(connection) as queries:
        response = owner.get(f"/api/c/{s.cmp}/orders")
    assert response.status_code == 200
    return len(response.json()["orders"]), len(queries)


def test_the_list_does_not_read_again_for_every_order(owner: Client, s: Scenario) -> None:
    _book(owner, s, s.vehicle, 1)
    one, few = _list_queries(owner, s)
    for day in range(3, 13, 2):
        _book(owner, s, s.vehicle, day)
    six, many = _list_queries(owner, s)
    assert (one, six) == (1, 6)
    assert many - few <= 0, f"{few} queries for 1 order, {many} for 6"
