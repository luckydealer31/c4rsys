"""Locations, contexts, state and schedule (ТЗ гл. 4, § 22.1.5–22.1.6)."""

from collections.abc import Iterator
from typing import Any
from unittest import mock

import pytest
from django.db import IntegrityError, transaction
from django.test import Client

from c4rsys.companies import readiness
from c4rsys.companies.models import ActivityContext, Company, Location, Position
from c4rsys.platform.models import AuditEvent
from tests.accounts.helpers import post
from tests.companies.helpers import create_company, named_user

pytestmark = pytest.mark.django_db

FULL_DAY = [{"from": "00:00", "to": "24:00"}]


@pytest.fixture
def cmp(owner: Client) -> str:
    public_id: str = create_company(owner, timezone="Asia/Bangkok").json()["company"]["public_id"]
    return public_id


@pytest.fixture
def rental_conditions_met() -> Iterator[None]:
    """Stands for the rental module conditions of § 4.3.1 (a current tariff, tested in rentals)."""
    with mock.patch.dict(
        readiness._checks, {ActivityContext.Type.RENTAL: lambda company_id, location_id: True}
    ):
        yield


def _create_location(client: Client, cmp: str, key: str = "l-1", **data: Any) -> Any:
    body = {"name": "Phuket", **data}
    return post(client, f"/api/c/{cmp}/locations", body, headers={"Idempotency-Key": key})


def _add_context(client: Client, cmp: str, location_id: str, kind: str, key: str = "x-1") -> Any:
    return post(
        client,
        f"/api/c/{cmp}/locations/{location_id}/contexts",
        {"type": kind},
        headers={"Idempotency-Key": key},
    )


def _patch_location(client: Client, cmp: str, location_id: str, data: dict[str, Any]) -> Any:
    return client.patch(
        f"/api/c/{cmp}/locations/{location_id}",
        data=data,
        content_type="application/json",
        headers={"Idempotency-Key": "lp-1"},
    )


def test_location_with_only_a_name_is_a_draft_with_24_7_schedule(owner: Client, cmp: str) -> None:
    # § 4.1.2–4.1.3, § 4.4.3, § 22.1.6.
    response = _create_location(owner, cmp)

    assert response.status_code == 201, response.content
    location = response.json()["location"]
    assert location["contexts"] == []
    assert location["state"] == "draft"
    assert location["address"] == ""
    assert location["schedule"]["exceptions"] == []
    assert location["schedule"]["weekly"] == {
        day: FULL_DAY for day in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    }


def test_timezone_is_proposed_from_company_and_may_be_overridden(owner: Client, cmp: str) -> None:
    # § 3.1.6, § 4.2.1.
    proposed = _create_location(owner, cmp, key="l-1").json()["location"]
    overridden = _create_location(owner, cmp, key="l-2", timezone="Europe/Moscow").json()

    assert proposed["timezone"] == "Asia/Bangkok"
    assert overridden["location"]["timezone"] == "Europe/Moscow"


def test_without_company_timezone_location_has_none(owner: Client) -> None:
    cmp = create_company(owner).json()["company"]["public_id"]

    location = _create_location(owner, cmp).json()["location"]

    assert location["timezone"] is None


def test_names_may_repeat(owner: Client, cmp: str) -> None:
    # § 4.1.4: the name may repeat; links use the location ID.
    _create_location(owner, cmp, key="l-1")
    _create_location(owner, cmp, key="l-2")

    names = [x["name"] for x in owner.get(f"/api/c/{cmp}/locations").json()["locations"]]

    assert names == ["Phuket", "Phuket"]


def test_location_becomes_active_with_timezone_and_ready_context(
    owner: Client, cmp: str, rental_conditions_met: None
) -> None:
    # § 4.4.3: time zone plus at least one ready context.
    location_id = _create_location(owner, cmp).json()["location"]["id"]

    location = _add_context(owner, cmp, location_id, "rental").json()["location"]

    assert location["contexts"][0]["type"] == "rental"
    assert location["contexts"][0]["ready"] is True
    assert location["state"] == "active"


def test_location_without_timezone_stays_draft(owner: Client, rental_conditions_met: None) -> None:
    cmp = create_company(owner).json()["company"]["public_id"]
    location_id = _create_location(owner, cmp).json()["location"]["id"]

    location = _add_context(owner, cmp, location_id, "rental").json()["location"]
    assert location["state"] == "draft"

    location = _patch_location(
        owner, cmp, location_id, {"timezone": "Asia/Bangkok", "expected_version": 1}
    ).json()["location"]
    assert location["state"] == "active"


def test_context_without_module_conditions_is_not_ready(owner: Client, cmp: str) -> None:
    # § 4.3.1: a rental context needs a current tariff (T07); service and warehouse have no
    # module yet. One unready context does not block another (§ 4.4.3).
    location_id = _create_location(owner, cmp).json()["location"]["id"]
    for key, kind in (("x-1", "rental"), ("x-2", "service"), ("x-3", "warehouse")):
        assert _add_context(owner, cmp, location_id, kind, key).status_code == 201

    location = owner.get(f"/api/c/{cmp}/locations/{location_id}").json()["location"]

    assert [c["ready"] for c in location["contexts"]] == [False, False, False]
    assert location["state"] == "draft"


def test_one_context_of_each_type_per_location(owner: Client, cmp: str) -> None:
    # § 4.1.7.
    location_id = _create_location(owner, cmp).json()["location"]["id"]
    _add_context(owner, cmp, location_id, "rental", "x-1")

    response = _add_context(owner, cmp, location_id, "rental", "x-2")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "CONTEXT_ALREADY_EXISTS"
    assert ActivityContext.objects.count() == 1


@pytest.mark.parametrize("kinds", [["service"], ["warehouse"], ["service", "warehouse"], []])
def test_contexts_are_chosen_at_creation_without_rental(
    owner: Client, cmp: str, kinds: list[str]
) -> None:
    # § 4.1.1, § 4.1.3: any combination of contexts or none; rental is not imposed.
    response = _create_location(owner, cmp, contexts=kinds)

    assert response.status_code == 201, response.content
    created = sorted(c["type"] for c in response.json()["location"]["contexts"])
    assert created == sorted(kinds)
    assert AuditEvent.objects.filter(action="location.context_added").count() == len(kinds)


@pytest.mark.parametrize("kinds", [["taxi"], ["rental", "rental"], "rental"])
def test_invalid_contexts_at_creation_create_nothing(owner: Client, cmp: str, kinds: Any) -> None:
    response = _create_location(owner, cmp, contexts=kinds)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "CONTEXT_TYPE_INVALID"
    assert Location.objects.count() == 0


def test_unknown_context_type_is_rejected(owner: Client, cmp: str) -> None:
    location_id = _create_location(owner, cmp).json()["location"]["id"]

    response = _add_context(owner, cmp, location_id, "taxi")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "CONTEXT_TYPE_INVALID"


def test_rename_keeps_id_and_is_journaled(owner: Client, cmp: str) -> None:
    location_id = _create_location(owner, cmp).json()["location"]["id"]

    response = _patch_location(owner, cmp, location_id, {"name": "Patong", "expected_version": 1})

    assert response.json()["location"]["id"] == location_id
    assert response.json()["location"]["version"] == 2
    event = AuditEvent.objects.get(action="location.changed")
    assert (event.before, event.after) == ({"name": "Phuket"}, {"name": "Patong"})
    assert event.company_id == Company.objects.get().id


def test_timezone_of_location_cannot_be_cleared(owner: Client, cmp: str) -> None:
    location_id = _create_location(owner, cmp).json()["location"]["id"]

    response = _patch_location(owner, cmp, location_id, {"timezone": "", "expected_version": 1})

    assert response.status_code == 400
    assert Location.objects.get().timezone == "Asia/Bangkok"


def test_creation_and_context_are_journaled(owner: Client, cmp: str) -> None:
    location_id = _create_location(owner, cmp).json()["location"]["id"]
    _add_context(owner, cmp, location_id, "rental")

    actions = list(
        AuditEvent.objects.filter(object_type__in=["location", "activity_context"])
        .order_by("occurred_at")
        .values_list("action", flat=True)
    )

    assert actions == ["location.created", "location.context_added"]


def test_location_of_another_company_is_not_found(owner: Client, cmp: str) -> None:
    # § 2.8.1: no access to another company's location, even with a known ID.
    location_id = _create_location(owner, cmp).json()["location"]["id"]
    other_cmp = create_company(owner, key="c-2").json()["company"]["public_id"]

    assert owner.get(f"/api/c/{other_cmp}/locations/{location_id}").status_code == 404
    assert _add_context(owner, other_cmp, location_id, "rental").status_code == 404
    assert owner.get(f"/api/c/{other_cmp}/locations").json()["locations"] == []


def test_stranger_cannot_list_locations(owner: Client, cmp: str) -> None:
    _create_location(owner, cmp)
    stranger = Client()
    named_user(stranger, "stranger@example.com")

    assert stranger.get(f"/api/c/{cmp}/locations").status_code == 404


def test_any_employee_reads_locations_but_only_settings_holder_changes_them(
    owner: Client, cmp: str
) -> None:
    # Decision 22.10.2026: every employee sees all locations; changes need company.settings,
    # which before T05 only the top-authority position holds.
    location_id = _create_location(owner, cmp).json()["location"]["id"]
    Position.objects.update(is_top_authority=False)

    assert owner.get(f"/api/c/{cmp}/locations").status_code == 200
    assert owner.get(f"/api/c/{cmp}/locations/{location_id}").status_code == 200
    assert _create_location(owner, cmp, key="l-9").status_code == 403
    assert _add_context(owner, cmp, location_id, "rental").status_code == 403


def test_context_must_belong_to_location_of_same_company(owner: Client, cmp: str) -> None:
    # A05: the database forbids cross-company links.
    _create_location(owner, cmp)
    other_cmp = create_company(owner, key="c-2").json()["company"]["public_id"]
    other = Company.objects.get(public_id=other_cmp)

    with pytest.raises(IntegrityError), transaction.atomic():
        ActivityContext.objects.create(
            company=other, location=Location.objects.get(), type="rental"
        )
