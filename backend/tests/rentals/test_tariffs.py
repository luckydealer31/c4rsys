"""Tariff catalog (ТЗ § 4.3.1, § 13.1–13.2, § 22.1.8, § 23.4.4)."""

from typing import Any

import pytest
from django.db import IntegrityError, transaction
from django.test import Client

from c4rsys.companies.models import Company, Location
from c4rsys.platform.models import AuditEvent
from c4rsys.rentals.models import Tariff, TariffRange
from tests.accounts.helpers import post
from tests.companies.helpers import create_company, hire, key

pytestmark = pytest.mark.django_db

DAILY = [{"from": 1, "to": 3, "rate": "1000"}, {"from": 4, "to": None, "rate": "900.50"}]


@pytest.fixture
def cmp(owner: Client) -> str:
    public_id: str = create_company(owner, timezone="Asia/Bangkok", main_currency="THB").json()[
        "company"
    ]["public_id"]
    return public_id


def _location(client: Client, cmp: str, name: str, rental: bool = True) -> str:
    location_id: str = post(
        client, f"/api/c/{cmp}/locations", {"name": name}, headers=key()
    ).json()["location"]["id"]
    if rental:
        post(client, f"/api/c/{cmp}/locations/{location_id}/contexts", {"type": "rental"}, key())
    return location_id


def _create(client: Client, cmp: str, **data: Any) -> Any:
    body = {"name": "Daily", "ranges": DAILY, **data}
    return post(client, f"/api/c/{cmp}/tariffs", body, headers=key())


def _patch(client: Client, cmp: str, tariff_id: str, data: dict[str, Any]) -> Any:
    return client.patch(
        f"/api/c/{cmp}/tariffs/{tariff_id}",
        data=data,
        content_type="application/json",
        headers=key(),
    )


def _state(client: Client, cmp: str, location_id: str) -> str:
    state: str = client.get(f"/api/c/{cmp}/locations/{location_id}").json()["location"]["state"]
    return state


def test_create_daily_tariff_from_issue(owner: Client, cmp: str) -> None:
    # § 13.2.2, § 13.2.5, § 22.1.8: name, currency, unit, scope, ranges.
    response = _create(owner, cmp)

    assert response.status_code == 201, response.content
    tariff = response.json()["tariff"]
    assert (tariff["unit"], tariff["day_mode"], tariff["currency"]) == ("day", "from_issue", "THB")
    assert tariff["all_locations"] is True
    assert tariff["ranges"] == [
        {"from": 1, "to": 3, "rate": "1000.0000"},
        {"from": 4, "to": None, "rate": "900.5000"},
    ]
    assert AuditEvent.objects.filter(action="tariff.created").count() == 1


def test_constant_rate_is_one_open_range(owner: Client, cmp: str) -> None:
    # § 13.2.5.
    response = _create(owner, cmp, ranges=[{"from": 1, "to": None, "rate": 1000}])

    assert response.status_code == 201


@pytest.mark.parametrize(
    ("ranges", "code"),
    [
        ([], "RANGES_REQUIRED"),
        ([{"from": 2, "to": None, "rate": "1"}], "RANGES_NOT_CONTIGUOUS"),
        (
            [{"from": 1, "to": 3, "rate": "1"}, {"from": 5, "to": None, "rate": "1"}],
            "RANGES_NOT_CONTIGUOUS",
        ),
        (
            [{"from": 1, "to": 3, "rate": "1"}, {"from": 3, "to": None, "rate": "1"}],
            "RANGES_NOT_CONTIGUOUS",
        ),
        ([{"from": 1, "to": 3, "rate": "1"}], "RANGES_LAST_NOT_OPEN"),
        (
            [{"from": 1, "to": None, "rate": "1"}, {"from": 2, "to": None, "rate": "1"}],
            "RANGES_ONLY_LAST_OPEN",
        ),
        (
            [{"from": 1, "to": 0, "rate": "1"}, {"from": 1, "to": None, "rate": "1"}],
            "RANGE_BOUND_INVALID",
        ),
        ([{"from": 1.5, "to": None, "rate": "1"}], "RANGE_BOUND_INVALID"),
        ([{"from": 1, "to": None, "rate": "0"}], "RATE_INVALID"),
        ([{"from": 1, "to": None, "rate": "-5"}], "RATE_INVALID"),
        ([{"from": 1, "to": None, "rate": 99.9}], "RATE_INVALID"),
        ([{"from": 1, "to": None, "rate": "1.00001"}], "RATE_TOO_PRECISE"),
    ],
)
def test_range_structure_of_13_2_8(owner: Client, cmp: str, ranges: Any, code: str) -> None:
    # § 13.2.8: from 1, no gaps or overlaps, last open; rate > 0 (decision 22.10.2026).
    response = _create(owner, cmp, ranges=ranges)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == code
    assert not Tariff.objects.exists()


def test_tariff_units_and_settlement_days(owner: Client, cmp: str) -> None:
    # § 13.1.4: minute, hour, day and month; settlement days need their daily boundary.
    for unit in ("minute", "hour", "month"):
        created = _create(owner, cmp, name=unit, unit=unit)
        assert created.status_code == 201, created.content
        assert (created.json()["tariff"]["unit"], created.json()["tariff"]["day_mode"]) == (
            unit,
            None,
        )
    without_time = _create(owner, cmp, name="Noon", day_mode="fixed_boundary")
    noon = _create(owner, cmp, name="Noon", day_mode="fixed_boundary", day_boundary="12:00")

    assert without_time.json()["error"]["code"] == "TIME_INVALID"
    assert noon.json()["tariff"]["day_boundary"] == "12:00"
    changed = owner.patch(
        f"/api/c/{cmp}/tariffs/{noon.json()['tariff']['id']}",
        data={"day_boundary": "10:00", "expected_version": 1},
        content_type="application/json",
        headers=key(),
    )
    assert changed.json()["error"]["code"] == "TARIFF_FIELD_IMMUTABLE"


def test_rental_context_is_ready_with_available_tariff(owner: Client, cmp: str) -> None:
    # § 4.3.1, § 4.4.3: the location becomes active with a current tariff available in it.
    phuket = _location(owner, cmp, "Phuket")
    krabi = _location(owner, cmp, "Krabi")
    assert _state(owner, cmp, phuket) == "draft"

    tariff = _create(owner, cmp, locations=[phuket]).json()["tariff"]

    assert _state(owner, cmp, phuket) == "active"
    assert _state(owner, cmp, krabi) == "draft"

    post(owner, f"/api/c/{cmp}/tariffs/{tariff['id']}/archive", {"expected_version": 1}, key())
    assert _state(owner, cmp, phuket) == "draft"  # § 4.4.4

    _create(owner, cmp, name="Company wide")
    assert _state(owner, cmp, phuket) == "active"
    assert _state(owner, cmp, krabi) == "active"


def test_change_name_ranges_and_scope(owner: Client, cmp: str) -> None:
    phuket = _location(owner, cmp, "Phuket")
    tariff = _create(owner, cmp).json()["tariff"]

    response = _patch(
        owner,
        cmp,
        tariff["id"],
        {
            "name": "Daily Phuket",
            "ranges": [{"from": 1, "to": None, "rate": "800"}],
            "locations": [phuket],
            "expected_version": 1,
        },
    )

    assert response.status_code == 200, response.content
    changed = response.json()["tariff"]
    assert changed["name"] == "Daily Phuket"
    assert changed["locations"] == [phuket] and changed["all_locations"] is False
    assert changed["ranges"] == [{"from": 1, "to": None, "rate": "800.0000"}]
    assert TariffRange.objects.count() == 1
    event = AuditEvent.objects.get(action="tariff.changed")
    assert event.after is not None
    assert set(event.after) == {"name", "ranges", "all_locations", "locations"}


def test_currency_and_unit_cannot_change(owner: Client, cmp: str) -> None:
    tariff = _create(owner, cmp).json()["tariff"]

    response = _patch(owner, cmp, tariff["id"], {"currency": "USD", "expected_version": 1})

    assert response.json()["error"]["code"] == "TARIFF_FIELD_IMMUTABLE"


def test_stale_version_is_refused(owner: Client, cmp: str) -> None:
    tariff = _create(owner, cmp).json()["tariff"]
    _patch(owner, cmp, tariff["id"], {"name": "A", "expected_version": 1})

    response = _patch(owner, cmp, tariff["id"], {"name": "B", "expected_version": 1})

    assert response.json()["error"]["code"] == "VERSION_CONFLICT"


def test_archive_and_restore(owner: Client, cmp: str) -> None:
    tariff = _create(owner, cmp).json()["tariff"]
    path = f"/api/c/{cmp}/tariffs/{tariff['id']}"

    archived = post(owner, f"{path}/archive", {"expected_version": 1}, key()).json()["tariff"]
    again = post(owner, f"{path}/archive", {"expected_version": 2}, key())
    restored = post(owner, f"{path}/restore", {"expected_version": 2}, key()).json()["tariff"]

    assert archived["state"] == "archived"
    assert again.json()["error"]["code"] == "TARIFF_STATE_UNCHANGED"
    assert restored["state"] == "active"
    actions = list(
        AuditEvent.objects.filter(object_type="tariff")
        .order_by("occurred_at")
        .values_list("action", flat=True)
    )
    assert actions == ["tariff.created", "tariff.archived", "tariff.restored"]


def test_view_create_edit_archive_are_separate_rights(owner: Client, cmp: str) -> None:
    # § 23.4.4: the rental manager views tariffs but does not manage the catalog.
    tariff = _create(owner, cmp).json()["tariff"]
    manager, _ = hire(owner, cmp, "m@example.com", [{"type": "set", "set": "rental_manager"}])
    driver, _ = hire(owner, cmp, "d@example.com", [{"type": "set", "set": "driver"}])

    assert [t["id"] for t in manager.get(f"/api/c/{cmp}/tariffs").json()["tariffs"]] == [
        tariff["id"]
    ]
    assert _create(manager, cmp).status_code == 403
    assert (
        _patch(manager, cmp, tariff["id"], {"name": "X", "expected_version": 1}).status_code == 403
    )
    archive = post(
        manager, f"/api/c/{cmp}/tariffs/{tariff['id']}/archive", {"expected_version": 1}, key()
    )
    assert archive.status_code == 403
    assert driver.get(f"/api/c/{cmp}/tariffs").json()["tariffs"] == []
    assert driver.get(f"/api/c/{cmp}/tariffs/{tariff['id']}").status_code == 404


def test_location_limited_rights_cover_only_their_tariffs(owner: Client, cmp: str) -> None:
    phuket = _location(owner, cmp, "Phuket")
    krabi = _location(owner, cmp, "Krabi")
    krabi_tariff = _create(owner, cmp, name="Krabi", locations=[krabi]).json()["tariff"]
    rights = [
        {"type": "grant", "permission": p, "locations": [phuket]}
        for p in ("tariff.view", "tariff.create", "tariff.edit")
    ]
    editor, _ = hire(owner, cmp, "e@example.com", rights)

    assert _create(editor, cmp, locations=[phuket]).status_code == 201
    assert _create(editor, cmp, name="Everywhere").status_code == 403
    assert _create(editor, cmp, locations=[phuket, krabi]).status_code == 403
    names = [t["name"] for t in editor.get(f"/api/c/{cmp}/tariffs").json()["tariffs"]]
    assert names == ["Daily"]
    assert editor.get(f"/api/c/{cmp}/tariffs/{krabi_tariff['id']}").status_code == 404


def test_tariff_location_of_another_company_is_rejected(owner: Client, cmp: str) -> None:
    other = create_company(owner, key="c-2", display_name="Other").json()["company"]["public_id"]
    foreign = _location(owner, other, "Foreign", rental=False)

    response = _create(owner, cmp, locations=[foreign])

    assert response.json()["error"]["code"] == "LOCATION_UNKNOWN"


def test_range_must_belong_to_tariff_of_same_company(owner: Client, cmp: str) -> None:
    # A05: the database forbids cross-company links.
    _create(owner, cmp)
    other = Company.objects.get(
        public_id=create_company(owner, key="c-2", display_name="Other").json()["company"][
            "public_id"
        ]
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        TariffRange.objects.create(
            company=other, tariff=Tariff.objects.get(), from_units=9, to_units=None, rate=1
        )
    assert Location.objects.count() == 0
