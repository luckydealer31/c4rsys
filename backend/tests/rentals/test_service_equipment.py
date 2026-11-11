"""Additional services with equipment from the warehouse and the automatic start and completion
of time services without equipment (ТЗ § 11.1.5, § 13.4.7, § 13.4.11–13.4.14; customer's decisions
of 11.11.2026)."""

from collections.abc import Iterator
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest import mock
from urllib.parse import urlencode

import pytest
from django.test import Client

from c4rsys.companies.models import ActivityContext
from c4rsys.finance.models import ChargeEntry
from c4rsys.platform.models import AuditEvent
from c4rsys.rentals import extra_lines as lines_module
from c4rsys.rentals import issue as issue_module
from c4rsys.rentals.models import ExtraServiceLine
from c4rsys.warehouse.models import Asset, StockMovement
from tests.accounts.helpers import post
from tests.companies.helpers import key
from tests.rentals.scenario import NOW, Scenario, add_location, build, issue

pytestmark = pytest.mark.django_db

CLOCK = SimpleNamespace(value=NOW)


@pytest.fixture(autouse=True)
def frozen_clock() -> Iterator[None]:
    CLOCK.value = NOW
    clock = SimpleNamespace(now=lambda: CLOCK.value)
    with (
        mock.patch.object(issue_module, "timezone", clock),
        mock.patch.object(lines_module, "timezone", clock),
    ):
        yield


@pytest.fixture
def s(owner: Client) -> Scenario:
    return build(owner)


def _storage(owner: Client, s: Scenario, location: str) -> str:
    post(owner, f"/api/c/{s.cmp}/locations/{location}/contexts", {"type": "warehouse"}, key())
    return str(ActivityContext.objects.get(location_id=location, type="warehouse").id)


def _units(owner: Client, s: Scenario, location: str, count: int = 1) -> list[str]:
    """Individually accounted units received into the storage of the location."""
    base = f"/api/c/{s.cmp}/warehouse"
    item = post(owner, f"{base}/items", {"name": "Child seat", "unit": "piece"}, key()).json()[
        "item"
    ]
    known = set(Asset.objects.values_list("id", flat=True))
    draft = post(
        owner,
        f"{base}/receipts",
        {
            "source": "initial",
            "context_id": _storage(owner, s, location),
            "lines": [{"item_id": item["id"], "quantity": count, "unit_cost": "500"}],
        },
        key(),
    ).json()["receipt"]
    posted = post(
        owner,
        f"{base}/receipts/{draft['id']}/post",
        {"expected_version": draft["version"]},
        key(),
    )
    assert posted.status_code == 200, posted.content
    found = Asset.objects.exclude(pk__in=known).order_by("public_id")
    assert len(found) == count
    return [str(a.id) for a in found]


def _service(owner: Client, s: Scenario, **data: Any) -> Any:
    body = {"name": "Child seat", "pricing": "time", "unit": "day", "price": "100", **data}
    return post(owner, f"/api/c/{s.cmp}/extra-services", body, key())


def _equipment_service(owner: Client, s: Scenario, assets: list[str], **data: Any) -> str:
    created = _service(owner, s, with_equipment=True, assets=assets, **data)
    assert created.status_code == 201, created.content
    return str(created.json()["service"]["id"])


def _standalone(owner: Client, s: Scenario, service: str, **data: Any) -> Any:
    body = {"service_id": service, "location_id": s.location, "client_id": s.client, **data}
    return post(owner, f"/api/c/{s.cmp}/service-lines", body, key())


def _period(start: datetime, hours: float) -> dict[str, str]:
    return {
        "period_start": start.isoformat(),
        "period_end": (start + timedelta(hours=hours)).isoformat(),
    }


def _line_post(owner: Client, s: Scenario, line: dict[str, Any], action: str, **data: Any) -> Any:
    body = {"expected_version": line["version"], **data}
    return post(owner, f"/api/c/{s.cmp}/service-lines/{line['id']}/{action}", body, key())


def _choices(owner: Client, s: Scenario, service: str, **params: str) -> list[dict[str, Any]]:
    query = urlencode({"location_id": s.location, **params})
    response = owner.get(f"/api/c/{s.cmp}/extra-services/{service}/equipment?{query}")
    assert response.status_code == 200, response.content
    units: list[dict[str, Any]] = response.json()["units"]
    return units


def _charged() -> Decimal:
    return sum(
        (c.amount for c in ChargeEntry.objects.filter(source_type="rental_service")), Decimal(0)
    )


def test_units_are_attached_to_a_service(owner: Client, s: Scenario) -> None:
    # § 13.4.7: existing units with their AST ID, no copies; one unit in several services; the
    # flag is fixed at creation, the list changes.
    first, second = _units(owner, s, s.location, 2)

    created = _service(owner, s, with_equipment=True, assets=[first])

    assert created.status_code == 201, created.content
    service = created.json()["service"]
    assert service["with_equipment"] is True
    assert service["assets"] == [first]
    unit = service["equipment"][0]
    assert unit["public_id"] == Asset.objects.get(pk=first).public_id
    assert unit["item_name"] == "Child seat"
    assert unit["location_id"] == s.location
    assert unit["state"] == "in_stock"
    listed = owner.get(f"/api/c/{s.cmp}/extra-services/equipment-units").json()["units"]
    assert {u["id"] for u in listed} == {first, second}
    url = f"/api/c/{s.cmp}/extra-services/{service['id']}"
    changed = owner.patch(
        url,
        data={"assets": [first, second], "expected_version": service["version"]},
        content_type="application/json",
        headers=key(),
    )
    assert changed.status_code == 200, changed.content
    assert sorted(changed.json()["service"]["assets"]) == sorted([first, second])
    immutable = owner.patch(
        url,
        data={"with_equipment": False, "expected_version": changed.json()["service"]["version"]},
        content_type="application/json",
        headers=key(),
    )
    assert immutable.json()["error"]["code"] == "SERVICE_FIELD_IMMUTABLE"
    other = _service(owner, s, name="GPS", with_equipment=True, assets=[first])
    assert other.status_code == 201, other.content
    assert Asset.objects.count() == 2
    plain = _service(owner, s, name="Wash", assets=[first])
    assert plain.json()["error"]["code"] == "SERVICE_WITHOUT_EQUIPMENT"
    unknown = _service(owner, s, name="X", with_equipment=True, assets=[s.vehicle])
    assert unknown.json()["error"]["code"] == "ASSET_UNKNOWN"


def test_unit_of_another_location_is_refused(owner: Client, s: Scenario) -> None:
    # Decision of 11.11.2026: only a unit lying in the storage of this location is chosen.
    here = _units(owner, s, s.location)[0]
    there = _units(owner, s, add_location(owner, s.cmp, "Samui"))[0]
    service = _equipment_service(owner, s, [here, there])
    period = _period(NOW + timedelta(days=1), 48)

    choices = {u["id"]: u for u in _choices(owner, s, service, **period)}
    refused = _standalone(owner, s, service, asset_id=there, **period)
    missing = _standalone(owner, s, service, **period)
    foreign = _standalone(owner, s, _equipment_service(owner, s, [here]), asset_id=there, **period)
    taken = _standalone(owner, s, service, asset_id=here, **period)

    assert choices[here]["available"] is True and choices[here]["reason"] is None
    assert choices[there]["available"] is False
    assert choices[there]["reason"] == "other_location"
    assert refused.json()["error"]["code"] == "EQUIPMENT_NOT_AVAILABLE"
    assert refused.json()["error"]["params"]["reason"] == "other_location"
    assert missing.json()["error"]["field"] == "asset_id"
    assert foreign.json()["error"]["code"] == "EQUIPMENT_NOT_ATTACHED"
    assert taken.status_code == 201, taken.content
    line = taken.json()["line"]
    assert line["equipment"]["id"] == here
    assert line["equipment"]["public_id"] == Asset.objects.get(pk=here).public_id
    assert ExtraServiceLine.objects.count() == 1


def test_overlapping_booking_is_refused_and_cancel_frees(owner: Client, s: Scenario) -> None:
    # § 13.4.7: occupancy by the unit, whatever the service; § 13.4.12: a cancel frees it.
    unit = _units(owner, s, s.location)[0]
    seat = _equipment_service(owner, s, [unit])
    other = _equipment_service(owner, s, [unit], name="Seat, premium")
    start = NOW + timedelta(days=1)
    booked = _standalone(owner, s, seat, asset_id=unit, **_period(start, 48)).json()["line"]

    overlapping = _standalone(
        owner, s, other, asset_id=unit, **_period(start + timedelta(hours=47), 24)
    )
    busy = _choices(owner, s, other, **_period(start + timedelta(hours=1), 2))
    after = _standalone(owner, s, other, asset_id=unit, **_period(start + timedelta(hours=48), 24))

    assert overlapping.json()["error"]["code"] == "EQUIPMENT_NOT_AVAILABLE"
    assert overlapping.json()["error"]["params"]["reason"] == "occupied"
    assert busy[0]["available"] is False and busy[0]["reason"] == "occupied"
    assert after.status_code == 201, after.content
    # The period of a booked line moves only where the unit is free.
    moved = owner.patch(
        f"/api/c/{s.cmp}/service-lines/{booked['id']}",
        data={
            "period_end": (start + timedelta(hours=50)).isoformat(),
            "expected_version": booked["version"],
        },
        content_type="application/json",
        headers=key(),
    )
    assert moved.json()["error"]["code"] == "EQUIPMENT_NOT_AVAILABLE"
    cancelled = _line_post(owner, s, booked, "cancel", reason="Client declined")
    assert cancelled.json()["line"]["state"] == "cancelled"
    again = _standalone(owner, s, other, asset_id=unit, **_period(start, 48))
    assert again.status_code == 201, again.content


def test_hand_out_and_return_in_another_location(owner: Client, s: Scenario) -> None:
    # § 13.4.11–13.4.14: an employee hands out and accepts the return; the cost by the actual
    # time; the unit lies where it was returned; the revenue stays with the hand-out location.
    unit = _units(owner, s, s.location)[0]
    samui = add_location(owner, s.cmp, "Samui")
    no_storage = add_location(owner, s.cmp, "Krabi")
    samui_storage = _storage(owner, s, samui)
    ActivityContext.objects.filter(location_id=no_storage, type="warehouse").update(
        state="disabled"
    )
    service = _equipment_service(owner, s, [unit])
    line = _standalone(
        owner, s, service, asset_id=unit, **_period(NOW - timedelta(days=1), 24)
    ).json()["line"]
    assert line["calculation"]["kind"] == "planned"
    assert _charged() == Decimal("100")

    handed = _line_post(owner, s, line, "start", at=(NOW - timedelta(days=3)).isoformat())

    assert handed.status_code == 200, handed.content
    line = handed.json()["line"]
    assert line["state"] == "started"
    assert line["calculation"]["kind"] == "current"
    assert line["calculation"]["amount"] == "300.00"  # three days up to now, not the period
    assert Asset.objects.get(pk=unit).state == "issued"
    # § 13.4.12: handed out and not returned — unavailable even after its paid period.
    later = _period(NOW + timedelta(days=30), 24)
    assert _choices(owner, s, service, **later)[0]["reason"] == "occupied"
    assert (
        _standalone(owner, s, service, asset_id=unit, **later).json()["error"]["code"]
        == "EQUIPMENT_NOT_AVAILABLE"
    )
    refused = _line_post(owner, s, line, "complete", location_id=no_storage)
    assert refused.json()["error"]["code"] == "RETURN_STORAGE_NOT_USABLE"

    returned = _line_post(owner, s, line, "complete", location_id=samui)

    assert returned.status_code == 200, returned.content
    line = returned.json()["line"]
    assert line["state"] == "completed"
    assert line["location_id"] == s.location  # § 13.4.14
    assert line["return_location_id"] == samui
    assert line["calculation"]["kind"] == "actual"
    assert line["calculation"]["units"] == 3
    assert _charged() == Decimal("300")
    asset = Asset.objects.get(pk=unit)
    assert asset.state == "in_stock"
    assert str(asset.context_id) == samui_storage
    kinds = list(
        StockMovement.objects.filter(asset_id=unit)
        .exclude(kind="receipt")
        .order_by("created_at")
        .values_list("kind", "quantity")
    )
    assert kinds == [("issue", Decimal("-1")), ("return", Decimal("1"))]
    # It now lies in Samui: Phuket cannot take it, Samui can.
    assert _choices(owner, s, service, **later)[0]["reason"] == "other_location"
    in_samui = _standalone(owner, s, service, asset_id=unit, location_id=samui, **later)
    assert in_samui.status_code == 201, in_samui.content


def test_equipment_is_not_linked_to_a_rental_yet(owner: Client, s: Scenario) -> None:
    service = _equipment_service(owner, s, _units(owner, s, s.location))
    rental = issue(owner, s).json()["rental"]

    linked = post(
        owner,
        f"/api/c/{s.cmp}/rentals/{rental['id']}/services",
        {"service_id": service, "expected_version": rental["version"]},
        key(),
    )
    offered = owner.get(f"/api/c/{s.cmp}/extra-services/applicable?rental_id={rental['id']}")

    assert linked.json()["error"]["code"] == "EQUIPMENT_SERVICE_STANDALONE_ONLY"
    assert offered.json()["services"] == []


def test_time_service_starts_and_completes_by_its_period(owner: Client, s: Scenario) -> None:
    # Decision of 11.11.2026: without equipment — by the period, without an employee; an order
    # without a client keeps its service booked; equipment and piece services stay manual.
    guide = _service(owner, s, name="Guide", unit="hour", price="10").json()["service"]["id"]
    wash = _service(owner, s, name="Wash", pricing="once", unit="", price="50").json()["service"]
    unit = _units(owner, s, s.location)[0]
    seat = _equipment_service(owner, s, [unit])
    start = NOW - timedelta(hours=1)
    with_client = _standalone(owner, s, guide, **_period(start, 2)).json()["line"]
    no_client = _standalone(owner, s, guide, client_id=None, **_period(start, 2)).json()["line"]
    future = _standalone(owner, s, guide, **_period(NOW + timedelta(days=1), 2)).json()["line"]
    manual = _standalone(owner, s, wash["id"]).json()["line"]
    equipment = _standalone(owner, s, seat, asset_id=unit, **_period(start, 2)).json()["line"]

    def states() -> dict[str, str]:
        return {str(x.id): x.state for x in ExtraServiceLine.objects.all()}

    assert lines_module.auto_progress() == (1, 0)
    started = ExtraServiceLine.objects.get(pk=with_client["id"])
    assert started.state == "started"
    assert started.actual_start == start
    assert states() == {
        with_client["id"]: "started",
        no_client["id"]: "planned",
        future["id"]: "planned",
        manual["id"]: "planned",
        equipment["id"]: "planned",
    }
    event = AuditEvent.objects.get(action="service_line.started")
    assert event.system_process == lines_module.AUTO_PROGRESS_PROCESS
    assert lines_module.auto_progress() == (0, 0)  # idempotent

    CLOCK.value = NOW + timedelta(hours=2)
    assert lines_module.auto_progress() == (0, 1)
    done = ExtraServiceLine.objects.get(pk=with_client["id"])
    assert done.state == "completed"
    assert done.actual_end == start + timedelta(hours=2)
    assert states()[no_client["id"]] == "planned"
    assert states()[equipment["id"]] == "planned"
    assert lines_module.auto_progress() == (0, 0)
    # Charged by the period, as before: two guides with a client, the wash and the seat.
    assert _charged() == Decimal("20") + Decimal("20") + Decimal("50") + Decimal("100")

    # The client is set: the booked service starts and completes at the next run.
    order = owner.get(f"/api/c/{s.cmp}/orders/{no_client['order_id']}").json()["order"]
    named = post(
        owner,
        f"/api/c/{s.cmp}/orders/{order['id']}/client",
        {"client_id": s.client, "expected_version": order["version"]},
        key(),
    )
    assert named.status_code == 200, named.content
    assert lines_module.auto_progress() == (1, 1)
    assert states()[no_client["id"]] == "completed"
