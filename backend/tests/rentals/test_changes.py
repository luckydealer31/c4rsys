"""Extension, change of the tariff and reopening of a rental (ТЗ § 13.6.2–13.6.5, § 14.5.8;
customer's decisions П1–П12 of 28.10.2026)."""

from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from django.test import Client

from c4rsys.companies.models import PermissionSetItem
from c4rsys.platform.models import AuditEvent
from c4rsys.rentals import bookings as bookings_module
from c4rsys.rentals import changes as changes_module
from c4rsys.rentals import extra_lines as lines_module
from c4rsys.rentals import issue as issue_module
from c4rsys.rentals import rentals as rentals_module
from c4rsys.rentals import returns as returns_module
from c4rsys.rentals import views as views_module
from c4rsys.rentals.models import Rental
from tests.accounts.helpers import post
from tests.companies.helpers import hire, key
from tests.rentals.scenario import ISSUE_AT, NOW, Scenario, build, issue

pytestmark = pytest.mark.django_db


@pytest.fixture
def s(owner: Client) -> Scenario:
    return build(owner)


@pytest.fixture(autouse=True)
def frozen() -> Iterator[None]:
    clock = SimpleNamespace(now=lambda: NOW)
    with (
        mock.patch.object(issue_module, "timezone", clock),
        mock.patch.object(returns_module, "timezone", clock),
        mock.patch.object(lines_module, "timezone", clock),
        mock.patch.object(changes_module, "timezone", clock),
        mock.patch.object(bookings_module, "timezone", clock),
        mock.patch.object(rentals_module, "timezone", clock),
        mock.patch.object(views_module, "timezone", clock),
    ):
        yield


def _days(days: float) -> str:
    return (ISSUE_AT + timedelta(days=days)).isoformat()


def _charged(owner: Client, s: Scenario, rental: dict[str, Any]) -> str | None:
    order = owner.get(f"/api/c/{s.cmp}/orders/{rental['order_id']}").json()["order"]
    return order["amounts"]["THB"]["charged"] if "THB" in order["amounts"] else None


def _extend(owner: Client, s: Scenario, rental: dict[str, Any], end: str, **extra: Any) -> Any:
    body = {
        "planned_end": end,
        "client_consent": True,
        "consent_comment": "Phone call",
        "expected_version": rental["version"],
        **extra,
    }
    return post(owner, f"/api/c/{s.cmp}/rentals/{rental['id']}/planned-end", body, key())


def _code(response: Any) -> str:
    code: str = response.json()["error"]["code"]
    return code


def _tariff(owner: Client, s: Scenario, rate: str, deposit: dict[str, Any] | None = None) -> str:
    tariff: str = post(
        owner,
        f"/api/c/{s.cmp}/tariffs",
        {"name": f"Rate {rate}", "ranges": [{"from": 1, "to": None, "rate": rate}]},
        key(),
    ).json()["tariff"]["id"]
    owner.put(
        f"/api/c/{s.cmp}/tariffs/{tariff}/vehicles/{s.vehicle}",
        data={"mileage": {"mode": "unlimited"}, "deposit": deposit or {"required": False}},
        content_type="application/json",
        headers=key(),
    )
    return tariff


def _change_tariff(owner: Client, s: Scenario, rental: dict[str, Any], tariff: str) -> Any:
    return post(
        owner,
        f"/api/c/{s.cmp}/rentals/{rental['id']}/tariff",
        {"tariff_id": tariff, "expected_version": rental["version"]},
        key(),
    )


def _return(owner: Client, s: Scenario, rental: dict[str, Any]) -> dict[str, Any]:
    returned = post(
        owner,
        f"/api/c/{s.cmp}/rentals/{rental['id']}/return",
        {
            "actual_end": NOW.isoformat(),
            "return_location_id": s.location,
            "odometer": "10300",
            "fuel": "8",
            "expected_version": rental["version"],
        },
        key(),
    )
    assert returned.status_code == 200, returned.content
    result: dict[str, Any] = returned.json()["rental"]
    return result


def _complete(owner: Client, s: Scenario, rental: dict[str, Any]) -> Any:
    return post(
        owner,
        f"/api/c/{s.cmp}/rentals/{rental['id']}/complete-formalization",
        {"expected_version": rental["version"]},
        key(),
    )


# --- Extension ------------------------------------------------------------------------------


def test_extension_needs_consent_and_charges_the_new_planned_period(
    owner: Client, s: Scenario
) -> None:
    rental = issue(owner, s, planned_end=_days(3)).json()["rental"]
    assert _charged(owner, s, rental) == "3000.00"

    # П4: the client's consent is marked with a comment on how it was given.
    refused = _extend(owner, s, rental, _days(5), client_consent=False)
    assert _code(refused) == "CLIENT_CONSENT_REQUIRED"
    refused = _extend(owner, s, rental, _days(5), consent_comment="")
    assert (_code(refused), refused.json()["error"]["field"]) == (
        "FIELD_REQUIRED",
        "consent_comment",
    )

    extended = _extend(owner, s, rental, _days(5))
    assert extended.status_code == 200, extended.content
    rental = extended.json()["rental"]
    assert rental["planned_end"].startswith("2026-11-06T03:00")
    assert _charged(owner, s, rental) == "5000.00"
    event = AuditEvent.objects.get(action="rental.planned_end_changed")
    assert (event.after or {})["consent_comment"] == "Phone call"

    # П2: the same action shortens the planned period, not to the current moment or before it.
    past = _extend(owner, s, rental, (NOW - timedelta(hours=1)).isoformat())
    assert _code(past) == "PLANNED_END_NOT_FUTURE"
    shortened = _extend(owner, s, rental, _days(4)).json()["rental"]
    assert _charged(owner, s, shortened) == "4000.00"


def test_planned_end_is_set_for_the_first_time(owner: Client, s: Scenario) -> None:
    rental = issue(owner, s).json()["rental"]
    assert _charged(owner, s, rental) is None

    rental = _extend(owner, s, rental, _days(3)).json()["rental"]
    assert _charged(owner, s, rental) == "3000.00"  # Р16: charged at once


def test_overdue_rental_is_extended_and_the_delay_stays_in_history(
    owner: Client, s: Scenario
) -> None:
    rental = issue(owner, s, planned_end=_days(1)).json()["rental"]
    assert owner.get(f"/api/c/{s.cmp}/rentals/{rental['id']}").json()["rental"]["overdue"]
    # П1: until the return the order keeps the planned period.
    assert _charged(owner, s, rental) == "1000.00"

    extended = _extend(owner, s, rental, _days(3))
    assert extended.status_code == 200, extended.content
    assert not extended.json()["rental"]["overdue"]
    event = AuditEvent.objects.get(action="rental.planned_end_changed")
    assert (event.before or {})["overdue"] is True


def test_extension_over_the_next_booking_is_refused(owner: Client, s: Scenario) -> None:
    rental = issue(owner, s, planned_end=_days(3)).json()["rental"]
    booked = post(
        owner,
        f"/api/c/{s.cmp}/bookings",
        {"vehicle_card_id": s.vehicle, "planned_start": _days(4), "planned_end": _days(6)},
        key(),
    )
    assert booked.status_code == 201, booked.content

    refused = _extend(owner, s, rental, _days(5))
    assert _code(refused) == "VEHICLE_NOT_AVAILABLE"
    assert refused.json()["error"]["params"]["conflicts"][0]["kind"] == "booking"
    assert _extend(owner, s, rental, _days(4)).status_code == 200


def test_rights_of_the_rental_manager(owner: Client, s: Scenario) -> None:
    # П12: the rental manager extends; changing the terms and reopening are granted apart.
    granted = set(
        PermissionSetItem.objects.filter(
            permission_set__code="rental_manager", permission_set__is_system=True
        ).values_list("permission_id", flat=True)
    )
    assert "rental.extend" in granted
    assert "rental.change_terms" not in granted
    assert "rental.reopen" not in granted

    manager, _ = hire(owner, s.cmp, "m@example.com", [{"type": "set", "set": "rental_manager"}])
    rental = issue(owner, s, planned_end=_days(3)).json()["rental"]
    assert _extend(manager, s, rental, _days(4)).status_code == 200
    rental = owner.get(f"/api/c/{s.cmp}/rentals/{rental['id']}").json()["rental"]
    assert _code(_change_tariff(manager, s, rental, s.tariff)) == "PERMISSION_DENIED"


# --- Tariff ---------------------------------------------------------------------------------


def test_tariff_change_recharges_from_the_start(owner: Client, s: Scenario) -> None:
    cheaper = _tariff(owner, s, "800")
    rental = issue(owner, s, planned_end=_days(3)).json()["rental"]

    assert _code(_change_tariff(owner, s, rental, s.tariff)) == "TARIFF_SAME"
    changed = _change_tariff(owner, s, rental, cheaper)
    assert changed.status_code == 200, changed.content
    rental = changed.json()["rental"]
    assert rental["tariff_id"] == cheaper
    assert rental["tariff_snapshot"]["tariff_id"] == cheaper
    assert _charged(owner, s, rental) == "2400.00"
    event = AuditEvent.objects.get(action="rental.tariff_changed")
    assert (event.before or {})["tariff"] == s.tariff

    # П6: also after the return until the formalization is completed.
    rental = _return(owner, s, rental)
    assert _charged(owner, s, rental) == "1600.00"
    rental = _change_tariff(owner, s, rental, s.tariff).json()["rental"]
    assert _charged(owner, s, rental) == "2000.00"
    rental = _complete(owner, s, rental).json()["rental"]
    assert _code(_change_tariff(owner, s, rental, cheaper)) == "RENTAL_FORMALIZATION_COMPLETED"


def test_tariff_not_available_for_the_vehicle_is_refused(owner: Client, s: Scenario) -> None:
    other: str = post(
        owner,
        f"/api/c/{s.cmp}/tariffs",
        {"name": "Not assigned", "ranges": [{"from": 1, "to": None, "rate": "500"}]},
        key(),
    ).json()["tariff"]["id"]
    rental = issue(owner, s, planned_end=_days(3)).json()["rental"]

    assert _code(_change_tariff(owner, s, rental, other)) == "TARIFF_NOT_AVAILABLE"


def test_tariff_change_keeps_the_deposit(owner: Client, s: Scenario) -> None:
    # П7: the required deposit stays as agreed at the issue.
    owner.put(
        f"/api/c/{s.cmp}/tariffs/{s.tariff}/vehicles/{s.vehicle}",
        data={"mileage": {"mode": "unlimited"}, "deposit": {"required": True, "amount": "5000"}},
        content_type="application/json",
        headers=key(),
    )
    other = _tariff(owner, s, "800", {"required": True, "amount": "9000"})
    rental = issue(owner, s, planned_end=_days(3)).json()["rental"]

    rental = _change_tariff(owner, s, rental, other).json()["rental"]

    assert rental["deposit"]["required"] == "5000.00"
    terms = rentals_module.deposit_terms(Rental.objects.get(pk=rental["id"]))
    assert (terms.amount, terms.currency) == (Decimal(5000), "THB")


def test_service_not_allowed_by_the_new_tariff_stays_with_a_warning(
    owner: Client, s: Scenario
) -> None:
    other = _tariff(owner, s, "800")
    service = post(
        owner,
        f"/api/c/{s.cmp}/extra-services",
        {
            "name": "Child seat",
            "pricing": "time",
            "unit": "day",
            "price": "100",
            "tariffs": [s.tariff],
        },
        key(),
    ).json()["service"]["id"]
    rental = issue(owner, s, planned_end=_days(3)).json()["rental"]
    rental = post(
        owner,
        f"/api/c/{s.cmp}/rentals/{rental['id']}/services",
        {"service_id": service, "expected_version": rental["version"]},
        key(),
    ).json()["rental"]
    assert rental["services_not_allowed"] == []

    rental = _change_tariff(owner, s, rental, other).json()["rental"]

    assert rental["services_not_allowed"] == [rental["services"][0]["id"]]
    assert rental["services"][0]["state"] != "cancelled"


# --- Reopening and correction ---------------------------------------------------------------


def test_reopened_rental_is_corrected_and_completed_again(owner: Client, s: Scenario) -> None:
    rental = _return(owner, s, issue(owner, s, planned_end=_days(3)).json()["rental"])
    assert _code(_reopen(owner, s, rental)) == "RENTAL_NOT_COMPLETED"
    rental = _complete(owner, s, rental).json()["rental"]
    assert _charged(owner, s, rental) == "2000.00"
    assert _code(_correct(owner, s, rental, _days(1))) == "RENTAL_NOT_REOPENED"

    assert _code(_reopen(owner, s, rental, reason="")) == "FIELD_REQUIRED"
    reopened = _reopen(owner, s, rental)
    assert reopened.status_code == 200, reopened.content
    rental = reopened.json()["rental"]
    assert rental["formalization"]["completed_at"] is None
    assert rental["reopened_at"] is not None
    # § 13.6.5: reopening changes neither the actual period nor the charge.
    assert _charged(owner, s, rental) == "2000.00"

    future = _correct(owner, s, rental, (NOW + timedelta(hours=1)).isoformat())
    assert _code(future) == "FUTURE_TIME_NOT_ALLOWED"
    corrected = _correct(owner, s, rental, _days(1))
    assert corrected.status_code == 200, corrected.content
    rental = corrected.json()["rental"]
    assert rental["actual_end"].startswith("2026-11-02T03:00")
    assert _charged(owner, s, rental) == "1000.00"
    assert AuditEvent.objects.filter(action="rental.corrected").count() == 1

    completed = _complete(owner, s, rental)
    assert completed.status_code == 200, completed.content
    assert _code(_correct(owner, s, completed.json()["rental"], _days(2))) == "RENTAL_NOT_REOPENED"


def test_correction_over_another_rental_is_refused(owner: Client, s: Scenario) -> None:
    first = issue(owner, s, planned_end=_days(1)).json()["rental"]
    first = post(
        owner,
        f"/api/c/{s.cmp}/rentals/{first['id']}/return",
        {
            "actual_end": _days(1),
            "return_location_id": s.location,
            "odometer": "10100",
            "fuel": "8",
            "expected_version": first["version"],
        },
        key(),
    ).json()["rental"]
    first = _complete(owner, s, first).json()["rental"]
    second = issue(
        owner, s, actual_start=_days(1.5), odometer="10100", planned_end=_days(3)
    ).json()["rental"]
    assert second["state"] == "active"
    first = _reopen(owner, s, first).json()["rental"]

    refused = _correct(owner, s, first, _days(2))
    assert _code(refused) == "VEHICLE_NOT_AVAILABLE"


def test_manual_charge_for_early_return_or_delay(owner: Client, s: Scenario) -> None:
    # П10: no automatic surcharge or penalty; a manual charge with its reason.
    rental = issue(owner, s, planned_end=_days(3)).json()["rental"]
    for kind in ("early_return", "overdue"):
        added = post(
            owner,
            f"/api/c/{s.cmp}/rentals/{rental['id']}/manual-charges",
            {
                "kind": kind,
                "amount": "100",
                "reason": "Agreed",
                "expected_version": rental["version"],
            },
            key(),
        )
        assert added.status_code == 201, added.content
        rental = added.json()["rental"]
    assert [c["kind"] for c in rental["manual_charges"]] == ["early_return", "overdue"]


def _reopen(owner: Client, s: Scenario, rental: dict[str, Any], reason: str = "Wrong end") -> Any:
    return post(
        owner,
        f"/api/c/{s.cmp}/rentals/{rental['id']}/reopen",
        {"reason": reason, "expected_version": rental["version"]},
        key(),
    )


def _correct(owner: Client, s: Scenario, rental: dict[str, Any], end: str) -> Any:
    return post(
        owner,
        f"/api/c/{s.cmp}/rentals/{rental['id']}/correct",
        {
            "actual_start": rental["actual_start"],
            "actual_end": end,
            "return_location_id": s.location,
            "place_kind": "location",
            "expected_version": rental["version"],
        },
        key(),
    )
