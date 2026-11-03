"""Audit item 2: changes of a booking take the vehicle first, so two commands that occupy the same
vehicle for overlapping times check its availability one after the other."""

import threading
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import psycopg
import pytest
from django.db import connection, connections
from django.test import Client
from django.utils import timezone

from c4rsys.fleet.models import VehicleCard
from tests.accounts.helpers import post
from tests.companies.helpers import key
from tests.rentals.scenario import Scenario, add_vehicle, build

# Two real transactions; the data of migrations (currencies) is put back for the other tests.
pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


def _at(days: float) -> str:
    return (timezone.now().replace(microsecond=0, second=0) + timedelta(days=days)).isoformat()


@pytest.fixture
def s(owner: Client) -> Scenario:
    return build(owner)


@pytest.fixture
def vehicle_held() -> Iterator[Any]:
    """Another transaction holds a vehicle card, as a parallel command would."""
    params = connection.get_connection_params()
    with psycopg.connect(**params, autocommit=False) as other:

        def hold(card_id: str) -> None:
            other.execute(
                f"SELECT 1 FROM {VehicleCard._meta.db_table} WHERE id = %s FOR UPDATE",  # noqa: S608
                [card_id],
            )

        yield hold, other


def _in_thread(work: Any) -> tuple[threading.Thread, dict[str, Any]]:
    result: dict[str, Any] = {}

    def run() -> None:
        try:
            result["response"] = work()
        finally:
            connections.close_all()

    thread = threading.Thread(target=run)
    thread.start()
    return thread, result


def _booking(owner: Client, s: Scenario, vehicle: str) -> dict[str, Any]:
    body = {"vehicle_card_id": vehicle, "planned_start": _at(1), "planned_end": _at(3)}
    response = post(owner, f"/api/c/{s.cmp}/bookings", body, key())
    assert response.status_code == 201, response.content
    rental: dict[str, Any] = response.json()["rental"]
    return rental


def test_a_change_of_dates_waits_for_the_vehicle(
    owner: Client, s: Scenario, vehicle_held: Any
) -> None:
    rental = _booking(owner, s, s.vehicle)
    hold, other = vehicle_held
    hold(s.vehicle)

    thread, result = _in_thread(
        lambda: owner.patch(
            f"/api/c/{s.cmp}/bookings/{rental['id']}",
            data={"expected_version": rental["version"], "planned_end": _at(4)},
            content_type="application/json",
            headers=key(),
        )
    )
    thread.join(1.5)
    assert thread.is_alive(), "the change did not wait for the vehicle"
    other.rollback()
    thread.join(30)
    assert result["response"].status_code == 200, result["response"].content


def test_a_replacement_waits_for_the_new_vehicle(
    owner: Client, s: Scenario, vehicle_held: Any
) -> None:
    rental = _booking(owner, s, s.vehicle)
    another = add_vehicle(owner, s, s.location, name="Honda City")
    hold, other = vehicle_held
    hold(another)

    thread, result = _in_thread(
        lambda: post(
            owner,
            f"/api/c/{s.cmp}/bookings/{rental['id']}/vehicle",
            {"expected_version": rental["version"], "vehicle_card_id": another},
            key(),
        )
    )
    thread.join(1.5)
    assert thread.is_alive(), "the replacement did not wait for the new vehicle"
    other.rollback()
    thread.join(30)
    assert result["response"].status_code == 200, result["response"].content
