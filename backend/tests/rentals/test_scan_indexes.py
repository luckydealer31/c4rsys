"""Audit item 13: the background scans over all companies and the list of rentals have indexes
they can use. The table holds many finished rentals and a few bookings, as it will in time, and
sequential scans are switched off: a query that falls out of its index (a changed filter or a
dropped index) shows another plan."""

import uuid
from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any

import pytest
from django.db import connection
from django.db.models import QuerySet
from django.test import Client
from django.utils import timezone

from c4rsys.rentals import rentals
from c4rsys.rentals.models import Rental
from c4rsys.service.models import OPEN_STATES, ServiceOperation
from tests.rentals.scenario import build, issue

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def no_seq_scan(owner: Client) -> Iterator[None]:
    s = build(owner)
    model = Rental.objects.get(pk=issue(owner, s).json()["rental"]["id"])
    COMPANY.append(model.company_id)
    fields = {f.attname: getattr(model, f.attname) for f in Rental._meta.fields}

    def copy(**values: Any) -> Rental:
        return Rental(**{**fields, "id": uuid.uuid4(), "actual_period": None, **values})

    rows = [copy(state="cancelled", cancelled_at=NOW) for _ in range(3000)]
    # Bookings spread over a year, one day each; a few of them past their auto-cancel moment.
    for day in range(300):
        start = NOW + timedelta(days=day - 5)
        rows.append(
            copy(
                state="booked",
                booking_number=day + 1,
                planned_start=start,
                planned_end=start + timedelta(hours=20),
                booking_period=(start, start + timedelta(hours=20)),
                auto_cancel_at=start + timedelta(hours=2),
                actual_start=None,
                actual_end=None,
            )
        )
    Rental.objects.bulk_create(rows)
    with connection.cursor() as cursor:
        cursor.execute("ANALYZE rental")
        cursor.execute("ANALYZE service_operation")
        cursor.execute("SET enable_seqscan = off")
    yield
    with connection.cursor() as cursor:
        cursor.execute("RESET enable_seqscan")


def plan(query: QuerySet[Any]) -> str:
    return query.explain()


NOW = timezone.now()
COMPANY: list[uuid.UUID] = []
SCANS: dict[str, Callable[[], QuerySet[Any]]] = {
    # bookings.cancel_no_shows
    "rental_booked_auto_cancel_idx": lambda: Rental.objects.filter(
        state="booked", auto_cancel_at__lte=NOW
    ),
    # rentals.notices: the coming issue and the schedule conflict
    "rental_booked_start_idx": lambda: Rental.objects.filter(
        state="booked", planned_start__gt=NOW, planned_start__lte=NOW + timedelta(days=90)
    ),
    # rentals.notices: the coming and the overdue return
    "rental_active_end_idx": lambda: Rental.objects.filter(
        state="active", planned_end__isnull=False, planned_end__lte=NOW + timedelta(days=90)
    ),
    # finance.deposits.release_expired
    "rental_returned_end_idx": lambda: rentals.returned_with_deposit_term(NOW),
    # service.notices: the overdue operation
    "service_operation_open_end_idx": lambda: ServiceOperation.objects.filter(
        state__in=OPEN_STATES, planned_end__lt=NOW
    ),
    # the list of the section: the newest 100
    "rental_company_start_idx": lambda: rentals.rentals_of(COMPANY[-1]).exclude(state="proposed")[
        :100
    ],
}


@pytest.mark.parametrize("index", sorted(SCANS))
def test_scan_uses_its_index(index: str) -> None:
    found = plan(SCANS[index]())
    assert index in found, found
    assert "Seq Scan" not in found, found
