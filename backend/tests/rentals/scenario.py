"""Fixture of the stage 1 scenario (docs/stage1/STAGE1_ACCEPTANCE_TESTS.md): company in
Asia/Bangkok with THB, location «Phuket» with a rental context, daily tariff 1 000 THB, Toyota
Yaris with unlimited mileage and no deposit, client Ivan Petrov."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from django.test import Client

from tests.accounts.helpers import post
from tests.companies.helpers import create_company, key

BKK = ZoneInfo("Asia/Bangkok")
ISSUE_AT = datetime(2026, 11, 1, 10, 0, tzinfo=BKK)
NOW = datetime(2026, 11, 3, 10, 0, tzinfo=BKK)


@dataclass
class Scenario:
    cmp: str
    location: str
    tariff: str
    vehicle: str
    client: str


def add_location(owner: Client, cmp: str, name: str) -> str:
    location: str = post(owner, f"/api/c/{cmp}/locations", {"name": name}, key()).json()[
        "location"
    ]["id"]
    post(owner, f"/api/c/{cmp}/locations/{location}/contexts", {"type": "rental"}, key())
    return location


def add_vehicle(owner: Client, s: Scenario, location: str, name: str = "Toyota Yaris") -> str:
    vehicle: str = post(
        owner,
        f"/api/c/{s.cmp}/vehicles",
        {"name": name, "location_id": location, "odometer_unit": "km"},
        key(),
    ).json()["vehicle"]["id"]
    owner.put(
        f"/api/c/{s.cmp}/tariffs/{s.tariff}/vehicles/{vehicle}",
        data={"mileage": {"mode": "unlimited"}, "deposit": {"required": False}},
        content_type="application/json",
        headers=key(),
    )
    return vehicle


def add_client(owner: Client, cmp: str, first: str = "Ivan", phone: str = "+66812345678") -> str:
    client: str = post(
        owner,
        f"/api/c/{cmp}/clients",
        {
            "kind": "person",
            "first_name": first,
            "last_name": "Petrov",
            "contacts": [{"kind": "phone", "value": phone}],
        },
        key(),
    ).json()["client"]["id"]
    return client


def build(owner: Client) -> Scenario:
    cmp = create_company(
        owner, display_name="Test Rent", timezone="Asia/Bangkok", main_currency="THB"
    ).json()["company"]["public_id"]
    location = add_location(owner, cmp, "Phuket")
    tariff = post(
        owner,
        f"/api/c/{cmp}/tariffs",
        {"name": "Daily", "ranges": [{"from": 1, "to": None, "rate": "1000"}]},
        key(),
    ).json()["tariff"]["id"]
    s = Scenario(cmp=cmp, location=location, tariff=tariff, vehicle="", client="")
    s.vehicle = add_vehicle(owner, s, location)
    s.client = add_client(owner, cmp)
    return s


def issue_body(s: Scenario, **overrides: Any) -> dict[str, Any]:
    body = {
        "vehicle_card_id": s.vehicle,
        "client_id": s.client,
        "tariff_id": s.tariff,
        "issue_location_id": s.location,
        "actual_start": ISSUE_AT.isoformat(),
        "odometer": "10000",
        "fuel": "8",
    }
    body.update(overrides)
    return {k: v for k, v in body.items() if v is not None}


def issue(
    client: Client, s: Scenario, headers: dict[str, str] | None = None, **overrides: Any
) -> Any:
    return post(
        client, f"/api/c/{s.cmp}/rentals/issue", issue_body(s, **overrides), headers or key()
    )
