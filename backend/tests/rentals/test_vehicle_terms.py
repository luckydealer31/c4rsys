"""Terms of a tariff for a vehicle (ТЗ § 13.1.2, § 13.1.6, § 13.4.1, § 22.1.8)."""

from typing import Any

import pytest
from django.test import Client

from c4rsys.platform.models import AuditEvent
from c4rsys.rentals.models import TariffVehicleTerms
from tests.accounts.helpers import post
from tests.companies.helpers import create_company, hire, key

pytestmark = pytest.mark.django_db

UNLIMITED_NO_DEPOSIT = {"mileage": {"mode": "unlimited"}, "deposit": {"required": False}}


@pytest.fixture
def setup(owner: Client) -> dict[str, str]:
    cmp = create_company(owner, timezone="Asia/Bangkok", main_currency="THB").json()["company"][
        "public_id"
    ]
    location = post(owner, f"/api/c/{cmp}/locations", {"name": "Phuket"}, key()).json()["location"][
        "id"
    ]
    post(owner, f"/api/c/{cmp}/locations/{location}/contexts", {"type": "rental"}, key())
    tariff = post(
        owner,
        f"/api/c/{cmp}/tariffs",
        {"name": "Daily", "ranges": [{"from": 1, "to": None, "rate": "1000"}]},
        key(),
    ).json()["tariff"]["id"]
    vehicle = post(
        owner,
        f"/api/c/{cmp}/vehicles",
        {"name": "Toyota Yaris", "location_id": location, "odometer_unit": "km"},
        key(),
    ).json()["vehicle"]["id"]
    return {"cmp": cmp, "tariff": tariff, "vehicle": vehicle}


def _put(client: Client, s: dict[str, str], body: dict[str, Any]) -> Any:
    return client.put(
        f"/api/c/{s['cmp']}/tariffs/{s['tariff']}/vehicles/{s['vehicle']}",
        data=body,
        content_type="application/json",
        headers=key(),
    )


def test_unlimited_mileage_without_deposit_is_an_explicit_choice(
    owner: Client, setup: dict[str, str]
) -> None:
    response = _put(owner, setup, UNLIMITED_NO_DEPOSIT)

    assert response.status_code == 200, response.content
    terms = response.json()["terms"]
    assert terms["mileage"] == {
        "mode": "unlimited",
        "included_per_unit": None,
        "overage_price": None,
    }
    assert terms["deposit"] == {"required": False, "amount": None, "hold_days": None}
    assert (terms["mileage_unit"], terms["currency"]) == ("km", "THB")


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"deposit": {"required": False}}, "mileage.mode"),
        ({"mileage": {"mode": "unlimited"}}, "deposit.required"),
        (
            {"mileage": {"mode": "included"}, "deposit": {"required": False}},
            "mileage.included_per_unit",
        ),
        (
            {
                "mileage": {"mode": "included", "included_per_unit": "200"},
                "deposit": {"required": False},
            },
            "mileage.overage_price",
        ),
        ({"mileage": {"mode": "unlimited"}, "deposit": {"required": True}}, "deposit.amount"),
        (
            {"mileage": {"mode": "unlimited"}, "deposit": {"required": False, "amount": "5000"}},
            "deposit.amount",
        ),
        (
            {"mileage": {"mode": "unlimited"}, "deposit": {"required": True, "amount": 5000.5}},
            "deposit.amount",
        ),
        (
            {
                "mileage": {"mode": "unlimited"},
                "deposit": {"required": True, "amount": "1", "hold_days": 0},
            },
            "deposit.hold_days",
        ),
    ],
)
def test_choices_and_values_are_validated(
    owner: Client, setup: dict[str, str], body: dict[str, Any], field: str
) -> None:
    response = _put(owner, setup, body)

    assert response.status_code == 400
    assert response.json()["error"]["field"] == field
    assert not TariffVehicleTerms.objects.exists()


def test_included_mileage_and_deposit_are_stored_and_replaced(
    owner: Client, setup: dict[str, str]
) -> None:
    # § 13.1.6, § 13.4.1; hold term in days, optional (decision 22.10.2026).
    full = {
        "mileage": {"mode": "included", "included_per_unit": "200", "overage_price": "5.5"},
        "deposit": {"required": True, "amount": "5000", "hold_days": 3},
    }
    first = _put(owner, setup, full).json()["terms"]
    second = _put(owner, setup, UNLIMITED_NO_DEPOSIT).json()["terms"]

    assert first["mileage"] == {
        "mode": "included",
        "included_per_unit": "200",
        "overage_price": "5.5",
    }
    assert first["deposit"] == {"required": True, "amount": "5000", "hold_days": 3}
    assert second["version"] == 2 and second["deposit"]["required"] is False
    assert TariffVehicleTerms.objects.count() == 1
    listed = owner.get(f"/api/c/{setup['cmp']}/tariffs/{setup['tariff']}/vehicles").json()
    assert [v["vehicle_card_id"] for v in listed["vehicles"]] == [setup["vehicle"]]
    assert AuditEvent.objects.filter(action="tariff.vehicle_terms_set").count() == 2


def test_assignment_needs_tariff_edit(owner: Client, setup: dict[str, str]) -> None:
    manager, _ = hire(
        owner, setup["cmp"], "m@example.com", [{"type": "set", "set": "rental_manager"}]
    )

    assert _put(manager, setup, UNLIMITED_NO_DEPOSIT).status_code == 403


def test_vehicle_of_another_company_is_not_found(owner: Client, setup: dict[str, str]) -> None:
    other = create_company(owner, key="c-2", display_name="Other").json()["company"]["public_id"]
    other_location = post(owner, f"/api/c/{other}/locations", {"name": "X"}, key()).json()[
        "location"
    ]["id"]
    foreign = post(
        owner,
        f"/api/c/{other}/vehicles",
        {"name": "Foreign", "location_id": other_location, "odometer_unit": "km"},
        key(),
    ).json()["vehicle"]["id"]

    response = _put(owner, {**setup, "vehicle": foreign}, UNLIMITED_NO_DEPOSIT)

    assert response.status_code == 404


def test_archived_tariff_drops_out_of_vehicle_tariffs(owner: Client, setup: dict[str, str]) -> None:
    # Decision 22.10.2026: no separate removal; archiving the tariff takes it off the vehicles.
    from c4rsys.fleet.models import VehicleCard
    from c4rsys.rentals.tariffs import tariffs_for_vehicle

    card = VehicleCard.objects.get(pk=setup["vehicle"])
    assert not tariffs_for_vehicle(card).exists()  # not assigned yet
    _put(owner, setup, UNLIMITED_NO_DEPOSIT)
    assert [str(t.id) for t in tariffs_for_vehicle(card)] == [setup["tariff"]]

    path = f"/api/c/{setup['cmp']}/tariffs/{setup['tariff']}"
    post(owner, f"{path}/archive", {"expected_version": 1, "confirm_unconfigured": True}, key())
    assert not tariffs_for_vehicle(card).exists()
    assert TariffVehicleTerms.objects.count() == 1

    post(owner, f"{path}/restore", {"expected_version": 2}, key())
    assert tariffs_for_vehicle(card).count() == 1


def test_prices_belong_to_the_vehicle(owner: Client, setup: dict[str, str]) -> None:
    """Customer's decision of 11.11.2026: a tariff has ranges without a price; every vehicle of
    the tariff has its own price in every range."""
    cmp = setup["cmp"]
    tariff = post(
        owner,
        f"/api/c/{cmp}/tariffs",
        {"name": "Ranges", "ranges": [{"from": 1, "to": 7}, {"from": 8, "to": None}]},
        key(),
    ).json()["tariff"]
    assert tariff["ranges"] == [
        {"from": 1, "to": 7, "rate": None},
        {"from": 8, "to": None, "rate": None},
    ]
    s = {**setup, "tariff": tariff["id"]}

    # No price, a price for a part of the ranges, a price for a range the tariff has not.
    assert _put(owner, s, UNLIMITED_NO_DEPOSIT).json()["error"]["code"] == "RATES_REQUIRED"
    partial = {**UNLIMITED_NO_DEPOSIT, "rates": [{"from": 1, "rate": "2500"}]}
    assert _put(owner, s, partial).json()["error"]["code"] == "RATES_REQUIRED"
    alien = {**UNLIMITED_NO_DEPOSIT, "rates": [{"from": 1, "rate": "1"}, {"from": 9, "rate": "1"}]}
    assert _put(owner, s, alien).json()["error"]["code"] == "RATES_REQUIRED"

    rates = [{"from": 1, "rate": "2500"}, {"from": 8, "rate": "2300"}]
    saved = _put(owner, s, {**UNLIMITED_NO_DEPOSIT, "rates": rates})
    assert saved.status_code == 200, saved.content
    assert saved.json()["terms"]["rates"] == [
        {"from": 1, "rate": "2500.0000"},
        {"from": 8, "rate": "2300.0000"},
    ]
    # The vehicle sees its own prices in the tariff offered for its rental.
    offered = owner.get(f"/api/c/{cmp}/vehicles/{s['vehicle']}/tariffs").json()["tariffs"]
    mine = next(item for item in offered if item["id"] == tariff["id"])
    assert [r["rate"] for r in mine["ranges"]] == ["2500.0000", "2300.0000"]

    # A range added later waits for the price of the vehicle; the kept one keeps its price.
    changed = owner.patch(
        f"/api/c/{cmp}/tariffs/{tariff['id']}",
        data={
            "ranges": [{"from": 1, "to": 7}, {"from": 8, "to": 30}, {"from": 31, "to": None}],
            "expected_version": tariff["version"],
        },
        content_type="application/json",
        headers=key(),
    )
    assert changed.status_code == 200, changed.content
    listed = owner.get(f"/api/c/{cmp}/tariffs/{tariff['id']}/vehicles").json()["vehicles"]
    assert [r["from"] for r in listed[0]["rates"]] == [1, 8]

    # The tariff is taken off the vehicle.
    url = f"/api/c/{cmp}/tariffs/{tariff['id']}/vehicles/{s['vehicle']}"
    removed = owner.delete(url, data={}, content_type="application/json", headers=key())
    assert removed.status_code == 200, removed.content
    assert owner.get(f"/api/c/{cmp}/tariffs/{tariff['id']}/vehicles").json()["vehicles"] == []
