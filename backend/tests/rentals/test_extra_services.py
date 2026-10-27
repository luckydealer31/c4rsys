"""Additional rental services: catalog, linked and standalone lines (ТЗ § 12.1.4,
§ 13.4.5–13.4.15, § 23.4.4; customer's decisions Т7, Т8, У1–У12 of 27.10.2026)."""

from collections.abc import Iterator
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from django.db import DatabaseError, transaction
from django.test import Client
from django.utils import timezone

from c4rsys.finance.models import ChargeEntry
from c4rsys.rentals import extra_lines as lines_module
from c4rsys.rentals import free_time as free_time_module
from c4rsys.rentals import issue as issue_module
from c4rsys.rentals import returns as returns_module
from c4rsys.rentals.models import ExtraServiceLine
from tests.accounts.helpers import post
from tests.companies.helpers import hire, key
from tests.rentals.scenario import ISSUE_AT, NOW, Scenario, add_vehicle, build, issue

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def frozen_clock() -> Iterator[None]:
    clock = SimpleNamespace(now=lambda: NOW)
    with (
        mock.patch.object(issue_module, "timezone", clock),
        mock.patch.object(returns_module, "timezone", clock),
        mock.patch.object(free_time_module, "timezone", clock),
        mock.patch.object(lines_module, "timezone", clock),
    ):
        yield


@pytest.fixture
def s(owner: Client) -> Scenario:
    return build(owner)


def _service(owner: Client, s: Scenario, **data: Any) -> Any:
    body = {"name": "Child seat", "pricing": "time", "unit": "day", "price": "100", **data}
    return post(owner, f"/api/c/{s.cmp}/extra-services", body, key())


def _link(owner: Client, s: Scenario, rental: dict[str, Any], service: str, **data: Any) -> Any:
    body = {"service_id": service, "expected_version": rental["version"], **data}
    return post(owner, f"/api/c/{s.cmp}/rentals/{rental['id']}/services", body, key())


def _standalone(owner: Client, s: Scenario, service: str, **data: Any) -> Any:
    body = {"service_id": service, "location_id": s.location, **data}
    return post(owner, f"/api/c/{s.cmp}/service-lines", body, key())


def _line_post(owner: Client, s: Scenario, line: dict[str, Any], action: str, **data: Any) -> Any:
    body = {"expected_version": line["version"], **data}
    return post(owner, f"/api/c/{s.cmp}/service-lines/{line['id']}/{action}", body, key())


def _return(owner: Client, s: Scenario, rental: dict[str, Any], **data: Any) -> Any:
    body = {
        "actual_end": NOW.isoformat(),
        "return_location_id": s.location,
        "odometer": "10300",
        "fuel": "8",
        "expected_version": rental["version"],
        **data,
    }
    return post(owner, f"/api/c/{s.cmp}/rentals/{rental['id']}/return", body, key())


def _charged() -> Decimal:
    return sum(
        (c.amount for c in ChargeEntry.objects.filter(source_type="rental_service")), Decimal(0)
    )


def _order(owner: Client, s: Scenario, order_id: str) -> dict[str, Any]:
    result: dict[str, Any] = owner.get(f"/api/c/{s.cmp}/orders/{order_id}").json()["order"]
    return result


def test_catalog_has_the_tariff_rights_and_fixed_pricing(owner: Client, s: Scenario) -> None:
    # § 23.4.4: the tariff rights; Т7: a rate per unit without ranges; У2: its own currency.
    created = _service(owner, s, currency="USD", locations=[s.location], tariffs=[s.tariff])
    driver, _ = hire(owner, s.cmp, "d@example.com", [{"type": "set", "set": "driver"}])

    assert created.status_code == 201, created.content
    service = created.json()["service"]
    assert service["currency"] == "USD"
    assert service["locations"] == [s.location]
    assert service["tariffs"] == [s.tariff]
    immutable = owner.patch(
        f"/api/c/{s.cmp}/extra-services/{service['id']}",
        data={"unit": "hour", "expected_version": service["version"]},
        content_type="application/json",
        headers=key(),
    )
    assert immutable.json()["error"]["code"] == "SERVICE_FIELD_IMMUTABLE"
    assert _service(driver, s).status_code == 403
    no_unit = _service(owner, s, unit="")
    assert no_unit.json()["error"]["field"] == "unit"


def test_linked_time_service_follows_the_rental(owner: Client, s: Scenario) -> None:
    # У4–У5: the rental's period, charged with the rental at the return; § 13.4.6, У9: the free
    # day of the rental applied to the chosen service.
    service = _service(owner, s).json()["service"]["id"]
    rental = issue(owner, s).json()["rental"]

    linked = _link(owner, s, rental, service)

    assert linked.status_code == 201, linked.content
    rental = linked.json()["rental"]
    line = rental["services"][0]
    assert line["state"] == "started"
    assert line["calculation"]["kind"] == "current"
    assert line["calculation"]["amount"] == "200.00"  # two days up to now
    assert _charged() == Decimal(0)  # charged with the rental at the return
    free = post(
        owner,
        f"/api/c/{s.cmp}/rentals/{rental['id']}/free-time",
        {
            "start": (ISSUE_AT + timedelta(hours=1)).isoformat(),
            "units": 1,
            "service_line_ids": [line["id"]],
            "expected_version": rental["version"],
        },
        key(),
    )
    assert free.status_code == 201, free.content
    returned = _return(owner, s, free.json()["rental"]).json()["rental"]
    line = returned["services"][0]
    assert line["state"] == "completed"
    assert line["calculation"]["units"] == 1
    assert _charged() == Decimal("100")
    order = _order(owner, s, returned["order_id"])
    assert {op["type"] for op in order["operations"]} == {"rental", "service"}


def test_applicability_by_vehicle_and_tariff(owner: Client, s: Scenario) -> None:
    # § 13.4.7, У3: within a kind «any of», between kinds «and».
    other = add_vehicle(owner, s, s.location, name="Honda Jazz")
    limited = _service(owner, s, vehicles=[other]).json()["service"]["id"]
    fits = _service(owner, s, name="GPS", vehicles=[s.vehicle], tariffs=[s.tariff])
    rental = issue(owner, s).json()["rental"]

    refused = _link(owner, s, rental, limited)
    choices = owner.get(f"/api/c/{s.cmp}/extra-services/applicable?rental_id={rental['id']}")

    assert refused.json()["error"]["code"] == "SERVICE_NOT_APPLICABLE"
    assert [x["name"] for x in choices.json()["services"]] == ["GPS"]
    assert _link(owner, s, rental, fits.json()["service"]["id"]).status_code == 201


def test_standalone_service_in_a_new_order(owner: Client, s: Scenario) -> None:
    # У1, У8, § 13.4.11: by quantity, charged at once; the order closes after its completion.
    service = _service(owner, s, name="Wash", pricing="quantity", unit="", price="50")
    service_id = service.json()["service"]["id"]

    created = _standalone(owner, s, service_id, client_id=s.client, quantity=3)

    assert created.status_code == 201, created.content
    line = created.json()["line"]
    assert line["state"] == "planned"
    assert line["location_id"] == s.location
    assert _charged() == Decimal("150")
    order = _order(owner, s, line["order_id"])
    assert order["state"] == "open"
    assert order["closing_conditions"][0]["operation"] == "service"
    changed = owner.patch(
        f"/api/c/{s.cmp}/service-lines/{line['id']}",
        data={"quantity": 2, "expected_version": line["version"]},
        content_type="application/json",
        headers=key(),
    )
    assert changed.status_code == 200, changed.content
    assert _charged() == Decimal("100")
    started = _line_post(owner, s, changed.json()["line"], "start")
    assert started.json()["line"]["state"] == "started"
    late_cancel = _line_post(owner, s, started.json()["line"], "cancel", reason="no")
    assert late_cancel.json()["error"]["code"] == "SERVICE_ALREADY_STARTED"
    done = _line_post(owner, s, started.json()["line"], "complete")
    assert done.json()["line"]["state"] == "completed"
    assert _order(owner, s, line["order_id"])["state"] == "ready_to_close"


def test_standalone_time_service_period_and_cancel(owner: Client, s: Scenario) -> None:
    # § 13.4.9–13.4.10: hours of its own period; § 13.4.15: cancelled before its start.
    service = _service(owner, s, name="Guide", unit="hour", price="10").json()["service"]["id"]
    start = NOW + timedelta(days=1)

    created = _standalone(
        owner,
        s,
        service,
        client_id=s.client,
        period_start=start.isoformat(),
        period_end=(start + timedelta(hours=2, minutes=10)).isoformat(),
    )

    assert created.status_code == 201, created.content
    line = created.json()["line"]
    assert line["calculation"]["units"] == 3
    assert _charged() == Decimal("30")
    cancelled = _line_post(owner, s, line, "cancel", reason="Client declined")
    assert cancelled.json()["line"]["state"] == "cancelled"
    assert _charged() == Decimal(0)
    assert _order(owner, s, line["order_id"])["state"] == "ready_to_close"


def test_unlink_after_issue_makes_it_standalone(owner: Client, s: Scenario) -> None:
    # § 13.4.9, У6: the same line, already started, with the rental's current period.
    service = _service(owner, s).json()["service"]["id"]
    rental = issue(owner, s).json()["rental"]
    line = _link(owner, s, rental, service).json()["rental"]["services"][0]

    unlinked = _line_post(owner, s, line, "unlink")

    assert unlinked.status_code == 200, unlinked.content
    line = unlinked.json()["line"]
    assert line["rental_id"] is None
    assert line["origin_rental_id"] == rental["id"]
    assert line["state"] == "started"
    assert datetime.fromisoformat(line["period"]["start"]) == ISSUE_AT
    assert datetime.fromisoformat(line["period"]["end"]) == NOW
    assert _charged() == Decimal("200")
    longer = owner.patch(
        f"/api/c/{s.cmp}/service-lines/{line['id']}",
        data={
            "period_end": (NOW + timedelta(days=1)).isoformat(),
            "expected_version": line["version"],
        },
        content_type="application/json",
        headers=key(),
    )
    assert longer.status_code == 200, longer.content
    assert _charged() == Decimal("300")


def test_cancelled_booking_leaves_its_services(owner: Client, s: Scenario) -> None:
    # § 13.4.15: standalone in the same order with the booking's period.
    service = _service(owner, s).json()["service"]["id"]
    base = timezone.now().replace(second=0, microsecond=0)
    booking = post(
        owner,
        f"/api/c/{s.cmp}/bookings",
        {
            "vehicle_card_id": s.vehicle,
            "client_id": s.client,
            "planned_start": (base + timedelta(days=10)).isoformat(),
            "planned_end": (base + timedelta(days=12)).isoformat(),
        },
        key(),
    ).json()["rental"]
    booking = _link(owner, s, booking, service).json()["rental"]
    assert booking["services"][0]["state"] == "planned"

    cancelled = post(
        owner,
        f"/api/c/{s.cmp}/bookings/{booking['id']}/cancel",
        {"reason": "Changed plans", "expected_version": booking["version"]},
        key(),
    )

    assert cancelled.status_code == 200, cancelled.content
    line = ExtraServiceLine.objects.get()
    assert line.rental_id is None
    assert str(line.origin_rental_id) == booking["id"]
    assert str(line.order_id) == booking["order_id"]
    assert line.state == "planned"
    assert line.period_end is not None and line.period_start is not None
    assert line.period_end - line.period_start == timedelta(days=2)
    assert _charged() == Decimal("200")


def test_snapshot_keeps_the_price_and_lines_stay(owner: Client, s: Scenario) -> None:
    # § 13.4.8: a later change of the catalog does not touch an added service; nothing deleted.
    created = _service(owner, s, pricing="once", unit="", price="300").json()["service"]
    line = _standalone(owner, s, created["id"], client_id=s.client).json()["line"]
    owner.patch(
        f"/api/c/{s.cmp}/extra-services/{created['id']}",
        data={"price": "500", "expected_version": created["version"]},
        content_type="application/json",
        headers=key(),
    )

    again = owner.get(f"/api/c/{s.cmp}/service-lines/{line['id']}").json()["line"]

    assert again["price"] == "300"
    assert again["calculation"]["amount"] == "300.00"
    with pytest.raises(DatabaseError), transaction.atomic():
        ExtraServiceLine.objects.filter(pk=line["id"]).delete()
