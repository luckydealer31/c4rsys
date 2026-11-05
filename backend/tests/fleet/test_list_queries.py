"""Audit item 12: the list of vehicles reads its data once for the list, not once a vehicle.
The load test (scripts/loadtest.sh) found the list at about 12 queries a vehicle; the summary of
a vehicle stays the same as without the batch."""

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from c4rsys.fleet import odometer, services
from c4rsys.fleet.models import VehicleCard
from c4rsys.fleet.views import _payload
from c4rsys.platform.commands import Actor, CommandContext
from tests.accounts.helpers import post
from tests.companies.helpers import create_company, key
from tests.fleet.test_owner_requests import answer, ask, person
from tests.rentals.scenario import ISSUE_AT, Scenario, add_vehicle, build, issue

pytestmark = pytest.mark.django_db

VIN = "JTDBT923771234567"


def _vehicle(owner: Client, s: Scenario, **data: Any) -> str:
    body = {"location_id": s.location, "odometer_unit": "km", "name": "Yaris", **data}
    created = post(owner, f"/api/c/{s.cmp}/vehicles", body, key())
    assert created.status_code == 201, created.content
    card_id: str = created.json()["vehicle"]["id"]
    return card_id


def _rich(owner: Client) -> Scenario:
    """Vehicles with all that the summary shows: a plate and a VIN another company also has, a
    managed vehicle with the owner's answers, a reading over an odometer replacement, a
    restriction."""
    s = build(owner)
    issued = issue(owner, s, actual_start=ISSUE_AT.isoformat(), odometer="10000")
    assert issued.status_code == 201, issued.content
    odometer.record_replacement(
        CommandContext(actor=Actor(user_id=uuid4()), command="test", idempotency_key="k"),
        VehicleCard.objects.get(pk=s.vehicle),
        replaced_at=ISSUE_AT + timedelta(days=1),
        old_final=Decimal("10100"),
        new_start=Decimal("0"),
        source_type="test",
        source_id=None,
    )
    restricted = add_vehicle(owner, s, s.location, name="Restricted")
    set_ = post(
        owner,
        f"/api/c/{s.cmp}/vehicles/{restricted}/restrictions",
        {"reason": "technical_fault", "forbid_rental_issue": True},
        key(),
    )
    assert set_.status_code == 201, set_.content
    _vehicle(owner, s, name="Matched", vin=VIN, plate="1กข 1234", plate_country="TH")
    other = create_company(owner, "c-2", display_name="Other Rent").json()["company"]["public_id"]
    place = post(owner, f"/api/c/{other}/locations", {"name": "Krabi"}, key())
    elsewhere = {"location_id": place.json()["location"]["id"], "odometer_unit": "km"}
    twin = post(owner, f"/api/c/{other}/vehicles", {**elsewhere, "name": "Twin", "vin": VIN}, key())
    assert twin.status_code == 201, twin.content
    somchai, usr = person("somchai@example.com")
    managed = _vehicle(
        owner, s, name="Managed", relation="managed", owner_kind="profile", owner_lookup=usr
    )
    sent = ask(owner, s.cmp, managed)
    assert sent.status_code == 201, sent.content
    assert answer(somchai, sent.json()["request"]["id"], ownership=True).status_code == 200
    return s


def test_list_rows_are_the_card_summaries(owner: Client) -> None:
    s = _rich(owner)
    rows = owner.get(f"/api/c/{s.cmp}/vehicles").json()["vehicles"]
    by_id = {row["id"]: row for row in rows}
    for card in services.cards_of(VehicleCard.objects.get(pk=s.vehicle).company_id):
        assert by_id[str(card.id)] == _payload(card), card.name
    shown = {row["name"]: row for row in rows}
    assert shown["Matched"]["characteristics"]["identifier_matches"] == ["vin"]
    assert shown["Managed"]["owner"]["ownership_confirmed"] is True
    assert shown["Restricted"]["restriction"] is not None
    reading = by_id[s.vehicle]["odometer"]
    assert reading is not None and reading["accumulated"] != reading["value"]


def test_list_queries_do_not_grow_with_the_vehicles(owner: Client) -> None:
    s = _rich(owner)

    def count() -> tuple[int, int]:
        with CaptureQueriesContext(connection) as captured:
            response = owner.get(f"/api/c/{s.cmp}/vehicles")
        assert response.status_code == 200
        return len(captured), len(response.json()["vehicles"])

    before, rows = count()
    for n in range(5):
        add_vehicle(owner, s, s.location, name=f"More {n}")
    after, more = count()
    assert more == rows + 5
    assert after == before, (before, after)
