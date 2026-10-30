"""Damages: only through an inspection, return or new, fixing by a service work, estimate and
charge in a rental, invalid source inspection (ТЗ § 10.5, § 10.6.3, § 16.5.4)."""

from typing import Any

import pytest
from django.db import DatabaseError, transaction
from django.test import Client

from c4rsys.finance.charges import current_charge
from c4rsys.inspections.models import Damage
from tests.accounts.helpers import post
from tests.companies.helpers import key
from tests.inspections.helpers import (
    act,
    at,
    code,
    create,
    created,
    damages_of,
    ok,
    photo,
    saved_with_damage,
)
from tests.rentals.scenario import NOW, Scenario, issue
from tests.service.test_service import _do, _job, _op, _work

pytestmark = pytest.mark.django_db


def test_damage_is_created_only_by_a_saved_inspection(owner: Client, s: Scenario) -> None:
    # No other way to create a damage (§ 10.5.2).
    assert post(owner, f"/api/c/{s.cmp}/damages", {}, key()).status_code == 404
    file_id = photo(owner)
    draft = created(
        owner,
        s,
        files=[{"file_id": file_id, "kind": "photo", "position": "front"}],
        damages=[{"zone_text": "Front bumper", "photo_file_ids": [file_id]}],
    )
    assert damages_of(owner, s) == []
    not_described = created(owner, s)
    refused = owner.patch(
        f"/api/c/{s.cmp}/inspections/{not_described['id']}",
        data={"expected_version": 1, "damages": [{"photo_file_ids": []}]},
        content_type="application/json",
        headers=key(),
    )
    assert code(refused) == "DAMAGE_NOT_DESCRIBED"

    ok(act(owner, s, draft, "complete", conducted=True))
    [damage] = damages_of(owner, s)
    assert (damage["state"], damage["zone_text"], damage["estimate"]) == (
        "actual",
        "Front bumper",
        None,
    )
    assert damage["history"][0]["kind"] == "created"
    assert damage["history"][0]["files"] == [{"file_id": file_id, "role": "selected"}]
    # The facts of the event never change (§ 10.5.2).
    with pytest.raises(DatabaseError), transaction.atomic():
        Damage.objects.filter(pk=damage["id"]).update(description="other")
    with pytest.raises(DatabaseError), transaction.atomic():
        Damage.objects.filter(pk=damage["id"]).delete()


def _fix(owner: Client, s: Scenario, fixes: list[dict[str, Any]]) -> Any:
    work = _work(owner, s, name="Body repair", price="900")
    op = _op(owner, s, [work["id"]])
    op = _do(owner, s, op, "accept", accepted_at=at(-3)).json()["operation"]
    op = _job(owner, s, op["jobs"][0], "start", actual_start=at(-2)).json()["operation"]
    return _job(
        owner,
        s,
        op["jobs"][0],
        "complete",
        actual_end=at(-1),
        odometer="10500",
        fixed_damages=fixes,
    )


def test_fixing_by_a_service_work_and_return(owner: Client, s: Scenario) -> None:
    first = saved_with_damage(owner, s, performed_at=at(-10), odometer="10000")
    other = saved_with_damage(owner, s, {"description": "Dent"}, performed_at=at(-9))
    damage_id = first["damages"][0]["id"]
    kept_id = other["damages"][0]["id"]

    result = photo(owner, "result")
    done = _fix(owner, s, [{"damage_id": damage_id, "photo_file_ids": [result]}])
    assert done.status_code == 200, done.content
    by_id = {d["id"]: d for d in damages_of(owner, s)}
    # Only the damages chosen explicitly are fixed (§ 16.5.4).
    assert (by_id[damage_id]["state"], by_id[kept_id]["state"]) == ("fixed", "actual")
    fixed = by_id[damage_id]["history"][-1]
    assert (fixed["kind"], fixed["basis_type"], fixed["odometer"]) == (
        "fixed",
        "service_job",
        "10500.0",
    )
    assert fixed["files"] == [{"file_id": result, "role": "fix_result"}]
    again = _fix(owner, s, [{"damage_id": damage_id}])
    assert code(again) == "DAMAGE_NOT_ACTUAL"

    # A later inspection records the damage at the same place: return the previous one.
    returned = saved_with_damage(owner, s, {"returns_damage_id": damage_id})
    assert returned["damages"] == []
    by_id = {d["id"]: d for d in damages_of(owner, s)}
    assert by_id[damage_id]["state"] == "actual"
    assert [e["kind"] for e in by_id[damage_id]["history"]] == ["created", "fixed", "returned"]
    # Not a fixed one any more; a new damage can be created instead.
    item = created(owner, s, damages=[{"returns_damage_id": damage_id}])
    assert code(act(owner, s, item, "complete")) == "DAMAGE_NOT_FIXED"
    new = saved_with_damage(owner, s, {"description": "Scratch again"})
    assert len(new["damages"]) == 1 and len(damages_of(owner, s)) == 3


def test_invalid_inspection_makes_its_damages_erroneous(owner: Client, s: Scenario) -> None:
    saved = saved_with_damage(owner, s)
    ok(act(owner, s, saved, "invalidate", reason="Another vehicle was photographed"))
    [damage] = damages_of(owner, s)
    assert damage["state"] == "erroneous"
    assert damage["history"][-1]["kind"] == "erroneous"
    assert damage["history"][-1]["comment"] == "Another vehicle was photographed"
    estimate = post(
        owner,
        f"/api/c/{s.cmp}/damages/{damage['id']}/estimate",
        {"estimate": {"amount": "10", "currency": "THB"}, "expected_version": damage["version"]},
        key(),
    )
    assert code(estimate) == "DAMAGE_ERRONEOUS"


def _rental_damage(owner: Client, s: Scenario, rental: dict[str, Any], **damage: Any) -> str:
    saved = saved_with_damage(
        owner,
        s,
        {"attributed_to_rental": True, **damage},
        kind="intermediate",
        application="rental",
        rental_id=rental["id"],
        performed_at=at(-12),
    )
    damage_id: str = saved["damages"][0]["id"]
    return damage_id


def _return(owner: Client, s: Scenario, rental: dict[str, Any]) -> dict[str, Any]:
    fresh = owner.get(f"/api/c/{s.cmp}/rentals/{rental['id']}").json()["rental"]
    response = post(
        owner,
        f"/api/c/{s.cmp}/rentals/{rental['id']}/return",
        {
            "actual_end": NOW.isoformat(),
            "return_location_id": s.location,
            "odometer": "10300",
            "fuel": "8",
            "expected_version": fresh["version"],
        },
        key(),
    )
    # § 10.5.5: the check does not block the return itself.
    assert response.status_code == 200, response.content
    found: dict[str, Any] = response.json()["rental"]
    return found


def _complete(owner: Client, s: Scenario, rental: dict[str, Any]) -> Any:
    fresh = owner.get(f"/api/c/{s.cmp}/rentals/{rental['id']}").json()["rental"]
    return post(
        owner,
        f"/api/c/{s.cmp}/rentals/{rental['id']}/complete-formalization",
        {"expected_version": fresh["version"]},
        key(),
    )


def _charged(owner: Client, s: Scenario, rental: dict[str, Any]) -> str:
    order = owner.get(f"/api/c/{s.cmp}/orders/{rental['order_id']}").json()["order"]
    amount: str = order["amounts"]["THB"]["charged"]
    return amount


def test_estimate_is_charged_and_waived_by_a_discount(owner: Client, s: Scenario) -> None:
    rental = issue(owner, s).json()["rental"]
    damage_id = _rental_damage(owner, s, rental)
    rental = _return(owner, s, rental)
    assert rental["formalization"]["missing"] == ["damage_estimate_missing"]
    assert code(_complete(owner, s, rental)) == "FORMALIZATION_INCOMPLETE"

    damage = owner.get(f"/api/c/{s.cmp}/damages/{damage_id}").json()["damage"]
    estimated = post(
        owner,
        f"/api/c/{s.cmp}/damages/{damage_id}/estimate",
        {"estimate": {"amount": "1500", "currency": "THB"}, "expected_version": damage["version"]},
        key(),
    )
    assert estimated.status_code == 200, estimated.content
    assert estimated.json()["damage"]["charged"] == "1500.0000"
    assert _charged(owner, s, rental) == "3500.00"

    # The client is released by a discount of 100 %; the charge and the estimate stay.
    discount = post(
        owner,
        f"/api/c/{s.cmp}/discounts",
        {"name": "No fault", "kind": "percent", "value": "100", "scope": "order"},
        key(),
    ).json()["discount"]
    applied = post(
        owner,
        f"/api/c/{s.cmp}/orders/{rental['order_id']}/discounts",
        {"discount_id": discount["id"], "position_type": "damage", "position_id": damage_id},
        key(),
    )
    assert applied.status_code == 200, applied.content
    assert _charged(owner, s, rental) == "2000.00"
    assert current_charge("damage", Damage.objects.get(pk=damage_id).id) == 1500
    assert _complete(owner, s, rental).status_code == 200


def test_damage_outside_a_rental_is_not_charged(owner: Client, s: Scenario) -> None:
    refused = create(owner, s, damages=[{"description": "x", "attributed_to_rental": True}])
    assert code(refused) == "DAMAGE_NOT_IN_RENTAL"
    saved = saved_with_damage(owner, s, {"estimate": {"amount": "700", "currency": "THB"}})
    [damage] = saved["damages"]
    # § 10.5.3: kept for accounting, no charge.
    assert (damage["estimate"], damage["charged"]) == (
        {"amount": "700.0000", "currency": "THB"},
        None,
    )
    assert current_charge("damage", Damage.objects.get(pk=damage["id"]).id) == 0


def test_explicit_zero_is_an_estimate(owner: Client, s: Scenario) -> None:
    rental = issue(owner, s).json()["rental"]
    _rental_damage(owner, s, rental, estimate={"amount": "0", "currency": "THB"})
    previous = _rental_damage(owner, s, rental, attributed_to_rental=False, description="Old")
    rental = _return(owner, s, rental)
    # A damage not charged in the rental needs no estimate (§ 10.5.5).
    assert previous and rental["formalization"]["missing"] == []
    assert _charged(owner, s, rental) == "2000.00"
    assert _complete(owner, s, rental).status_code == 200
