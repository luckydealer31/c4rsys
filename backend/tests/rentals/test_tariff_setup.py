"""The page of a tariff saved with one button (customer's decision of 19.11.2026): name,
availability, ranges and the prices of the vehicles in one command."""

from typing import Any
from uuid import UUID

import pytest
from django.test import Client

from c4rsys.platform.models import AuditEvent
from c4rsys.rentals.models import TariffVehicleTerms
from tests.accounts.helpers import post
from tests.companies.helpers import create_company, key

pytestmark = pytest.mark.django_db

FREE = {"mileage": {"mode": "unlimited"}, "deposit": {"required": False}}


@pytest.fixture
def s(owner: Client) -> dict[str, str]:
    cmp = create_company(owner, timezone="Asia/Bangkok", main_currency="THB").json()["company"][
        "public_id"
    ]
    location = post(owner, f"/api/c/{cmp}/locations", {"name": "Phuket"}, key()).json()["location"][
        "id"
    ]
    tariff = post(
        owner,
        f"/api/c/{cmp}/tariffs",
        {"name": "Minutes", "unit": "minute", "ranges": [{"from": 1, "to": 60}, {"from": 61}]},
        key(),
    ).json()["tariff"]["id"]
    ids = {}
    for name in ("Yaris", "Polo"):
        ids[name] = post(
            owner,
            f"/api/c/{cmp}/vehicles",
            {"name": name, "location_id": location, "odometer_unit": "km"},
            key(),
        ).json()["vehicle"]["id"]
    # Yaris has the tariff with a price in both ranges.
    response = owner.put(
        f"/api/c/{cmp}/tariffs/{tariff}/vehicles/{ids['Yaris']}",
        data={"rates": [{"from": 1, "rate": "20"}, {"from": 61, "rate": "18"}], **FREE},
        content_type="application/json",
        headers=key(),
    )
    assert response.status_code == 200, response.content
    return {"cmp": cmp, "tariff": tariff, "location": location, **ids}


def _setup(client: Client, s: dict[str, str], body: dict[str, Any]) -> Any:
    full = {"expected_version": 1, "name": "Minutes", "locations": "all", **body}
    return client.put(
        f"/api/c/{s['cmp']}/tariffs/{s['tariff']}/setup",
        data=full,
        content_type="application/json",
        headers=key(),
    )


def _rates(s: dict[str, str], vehicle: str) -> list[tuple[int, str]]:
    terms = TariffVehicleTerms.objects.get(tariff_id=s["tariff"], vehicle_card_id=UUID(s[vehicle]))
    return [
        (lower, f"{rate:.0f}")
        for lower, rate in terms.rates.order_by("from_units").values_list("from_units", "rate")
    ]


def test_ranges_and_prices_change_together(owner: Client, s: dict[str, str]) -> None:
    # A range is added at the end and the first boundary moves; Polo gets the tariff.
    ranges = [{"from": 1, "to": 90}, {"from": 91, "to": 600}, {"from": 601}]
    rates = [{"from": 1, "rate": "20"}, {"from": 91, "rate": "18"}, {"from": 601, "rate": "15"}]
    response = _setup(
        owner,
        s,
        {
            "name": "Minutes 2",
            "ranges": ranges,
            "vehicles": [
                {"vehicle_card_id": s["Yaris"], "rates": rates, **FREE},
                {"vehicle_card_id": s["Polo"], "rates": rates, **FREE},
            ],
        },
    )

    assert response.status_code == 200, response.content
    tariff = response.json()["tariff"]
    assert tariff["name"] == "Minutes 2"
    assert [(r["from"], r["to"]) for r in tariff["ranges"]] == [(1, 90), (91, 600), (601, None)]
    assert _rates(s, "Yaris") == _rates(s, "Polo") == [(1, "20"), (91, "18"), (601, "15")]
    actions = list(AuditEvent.objects.order_by("id").values_list("action", flat=True))
    assert actions[-3:] == [
        "tariff.changed",
        "tariff.vehicle_terms_set",
        "tariff.vehicle_terms_set",
    ]


def test_a_vehicle_left_without_a_price_keeps_everything_as_it_was(
    owner: Client, s: dict[str, str]
) -> None:
    # The ranges change, but Yaris keeps the tariff without prices for the new ranges.
    response = _setup(owner, s, {"ranges": [{"from": 1, "to": 90}, {"from": 91}], "vehicles": []})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "RATES_REQUIRED"
    assert _rates(s, "Yaris") == [(1, "20"), (61, "18")]
    detail = owner.get(f"/api/c/{s['cmp']}/tariffs/{s['tariff']}").json()["tariff"]
    assert [(r["from"], r["to"]) for r in detail["ranges"]] == [(1, 60), (61, None)]


def test_prices_must_follow_the_new_ranges(owner: Client, s: dict[str, str]) -> None:
    response = _setup(
        owner,
        s,
        {
            "ranges": [{"from": 1, "to": 90}, {"from": 91}],
            "vehicles": [
                {
                    "vehicle_card_id": s["Yaris"],
                    "rates": [{"from": 1, "rate": "20"}, {"from": 61, "rate": "18"}],
                    **FREE,
                }
            ],
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "RATES_REQUIRED"


def test_a_removed_vehicle_loses_the_tariff(owner: Client, s: dict[str, str]) -> None:
    response = _setup(
        owner, s, {"ranges": [{"from": 1, "to": 60}, {"from": 61}], "removed": [s["Yaris"]]}
    )

    assert response.status_code == 200, response.content
    assert not TariffVehicleTerms.objects.filter(tariff_id=s["tariff"]).exists()


def test_nothing_changed_runs_no_command(owner: Client, s: dict[str, str]) -> None:
    before = AuditEvent.objects.count()
    response = _setup(
        owner,
        s,
        {
            "ranges": [{"from": 1, "to": 60}, {"from": 61}],
            "vehicles": [
                {
                    "vehicle_card_id": s["Yaris"],
                    "rates": [{"from": 1, "rate": "20.00"}, {"from": 61, "rate": "18"}],
                    **FREE,
                }
            ],
        },
    )

    assert response.status_code == 200, response.content
    assert response.json()["tariff"]["version"] == 1
    assert AuditEvent.objects.count() == before


def test_an_old_version_is_refused(owner: Client, s: dict[str, str]) -> None:
    response = _setup(
        owner,
        s,
        {"expected_version": 7, "name": "Other", "ranges": [{"from": 1, "to": 60}, {"from": 61}]},
    )

    assert response.status_code == 409


def test_currency_does_not_change(owner: Client, s: dict[str, str]) -> None:
    response = _setup(owner, s, {"currency": "USD", "ranges": [{"from": 1}]})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "TARIFF_FIELD_IMMUTABLE"
