"""Audit item 12: the list of rentals reads a few queries a row, not the whole card of each.
The load test (scripts/loadtest.sh) found the list at about 27 queries a row: 2 700 for 100 rows
and 6.5 s an answer with 50 employees at once."""

from datetime import timedelta

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from tests.accounts.helpers import post
from tests.companies.helpers import key
from tests.rentals.scenario import add_vehicle, build, issue

pytestmark = pytest.mark.django_db


def queries(owner: Client, cmp: str) -> tuple[int, int]:
    with CaptureQueriesContext(connection) as captured:
        response = owner.get(f"/api/c/{cmp}/rentals")
    assert response.status_code == 200
    return len(captured), len(response.json()["rentals"])


def test_list_reads_a_few_queries_a_row(owner: Client) -> None:
    s = build(owner)
    start = timezone.now() - timedelta(days=30)
    for n in range(6):
        s.vehicle = add_vehicle(owner, s, s.location, name=f"Car {n}")
        rental = issue(owner, s, actual_start=start.isoformat()).json()["rental"]
        if n % 2:
            back = post(
                owner,
                f"/api/c/{s.cmp}/rentals/{rental['id']}/return",
                {
                    "actual_end": (start + timedelta(days=2)).isoformat(),
                    "return_location_id": s.location,
                    "odometer": "10000",
                    "fuel": "8",
                    "expected_version": rental["version"],
                },
                key(),
            )
            assert back.status_code == 200, back.content
    count, rows = queries(owner, s.cmp)
    assert rows == 6
    # The deposit of a row is still read row by row (3 queries); everything else once a list.
    assert count <= 12 + 3 * rows, count


def test_list_row_is_the_summary_of_the_interface(owner: Client) -> None:
    s = build(owner)
    issue(owner, s)
    [row] = owner.get(f"/api/c/{s.cmp}/rentals").json()["rentals"]
    # RentalSummary of frontend/src/api/types.ts; the card is GET /rentals/<id>.
    assert set(row) == {
        "id",
        "order_id",
        "vehicle_card_id",
        "client_id",
        "state",
        "issue_location_id",
        "return_location_id",
        "actual_start",
        "planned_end",
        "actual_end",
        "issue_exception",
        "deposit",
        "booking",
    }
    detail = owner.get(f"/api/c/{s.cmp}/rentals/{row['id']}").json()["rental"]
    assert "calculation" in detail
