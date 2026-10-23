"""Final readiness check of the initial setup (ТЗ § 22.1.12)."""

import pytest
from django.test import Client

from tests.accounts.helpers import post
from tests.companies.helpers import create_company, hire, key
from tests.rentals.scenario import add_location, build

pytestmark = pytest.mark.django_db


def test_new_company_needs_settings(owner: Client) -> None:
    cmp = create_company(owner).json()["company"]["public_id"]

    body = owner.get(f"/api/c/{cmp}/readiness").json()

    assert body["company"] == {
        "items": {"display_name": True, "timezone": False, "main_currency": False},
        "ready": False,
    }
    assert body["locations"] == [] and body["first_operation_ready"] is False


def test_rental_context_needs_tariff_and_suitable_vehicle(owner: Client) -> None:
    cmp = create_company(owner, timezone="Asia/Bangkok", main_currency="THB").json()["company"][
        "public_id"
    ]
    location = add_location(owner, cmp, "Phuket")
    body = owner.get(f"/api/c/{cmp}/readiness").json()
    assert body["locations"][0]["contexts"][0]["missing"] == ["no_tariff", "no_vehicle"]

    tariff = post(
        owner,
        f"/api/c/{cmp}/tariffs",
        {"name": "Daily", "ranges": [{"from": 1, "to": None, "rate": "1000"}]},
        key(),
    ).json()["tariff"]["id"]
    vehicle = post(
        owner,
        f"/api/c/{cmp}/vehicles",
        {"name": "Yaris", "location_id": location, "odometer_unit": "km"},
        key(),
    ).json()["vehicle"]["id"]
    body = owner.get(f"/api/c/{cmp}/readiness").json()
    assert body["locations"][0]["contexts"][0]["missing"] == ["no_vehicle_with_tariff"]

    owner.put(
        f"/api/c/{cmp}/tariffs/{tariff}/vehicles/{vehicle}",
        data={"mileage": {"mode": "unlimited"}, "deposit": {"required": False}},
        content_type="application/json",
        headers=key(),
    )
    body = owner.get(f"/api/c/{cmp}/readiness").json()
    assert body["locations"][0]["contexts"][0] == {
        "id": body["locations"][0]["contexts"][0]["id"],
        "type": "rental",
        "ready": True,
        "missing": [],
    }
    assert body["first_operation_ready"] is True


def test_unready_context_does_not_block_another(owner: Client) -> None:
    s = build(owner)
    post(owner, f"/api/c/{s.cmp}/locations/{s.location}/contexts", {"type": "service"}, key())

    contexts = owner.get(f"/api/c/{s.cmp}/readiness").json()["locations"][0]["contexts"]

    assert [(c["type"], c["ready"]) for c in contexts] == [("rental", True), ("service", False)]
    assert contexts[1]["missing"] == ["module_not_available"]


def test_readiness_needs_company_settings(owner: Client) -> None:
    s = build(owner)
    manager, _ = hire(owner, s.cmp, "m@example.com", [{"type": "set", "set": "rental_manager"}])

    assert manager.get(f"/api/c/{s.cmp}/readiness").status_code == 403


def test_setup_is_completed_only_with_a_ready_context(owner: Client) -> None:
    cmp = create_company(owner, timezone="Asia/Bangkok", main_currency="THB").json()["company"][
        "public_id"
    ]

    refused = post(owner, f"/api/c/{cmp}/setup/complete", {}, key())
    assert refused.json()["error"]["code"] == "SETUP_NOT_READY"
    assert owner.get(f"/api/c/{cmp}").json()["company"]["setup_completed_at"] is None


def test_completed_setup_and_context_types_are_in_the_company(owner: Client) -> None:
    s = build(owner)
    s_vehicle_terms = owner.put(
        f"/api/c/{s.cmp}/tariffs/{s.tariff}/vehicles/{s.vehicle}",
        data={"mileage": {"mode": "unlimited"}, "deposit": {"required": False}},
        content_type="application/json",
        headers=key(),
    )
    assert s_vehicle_terms.status_code in (200, 201)

    done = post(owner, f"/api/c/{s.cmp}/setup/complete", {}, key())

    assert done.status_code == 200, done.content
    company = owner.get(f"/api/c/{s.cmp}").json()["company"]
    assert company["setup_completed_at"] is not None
    assert company["context_types"] == ["rental"]
