"""Block 15 in the service: materials added to a planned work with an automatic or a manual
reserve (§ 11.7.7, § 11.7.1), parts installed on and removed from the vehicle with their periods,
replacement, valuation and write-off (§ 11.4.3, § 11.4.7, § 11.4.8), costs of materials with the
mark «incomplete» (§ 11.2.9, § 11.4.4), a return into another warehouse (§ 11.4.5), materials of
an external service from its own stock (§ 16.5.3)."""

from collections.abc import Iterator
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest import mock
from uuid import UUID

import pytest
from django.test import Client

from c4rsys.warehouse import parts as parts_module
from c4rsys.warehouse.models import (
    Asset,
    LotStock,
    PartInstallation,
    StockReserve,
)
from tests.accounts.helpers import post
from tests.companies.helpers import key
from tests.rentals.scenario import NOW, Scenario
from tests.service.test_service import _at, _code, _do, _job, _ok, _op, _work
from tests.warehouse.test_operations import item, receive_stock

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def frozen_parts() -> Iterator[None]:
    """The works of the scenario happen at its fixed moment, not at the real date."""
    with mock.patch.object(parts_module, "timezone", SimpleNamespace(now=lambda: NOW)):
        yield


def _warehouse(owner: Client, s: Scenario, location: str) -> str:
    post(owner, f"/api/c/{s.cmp}/locations/{location}/contexts", {"type": "warehouse"}, key())
    found = owner.get(f"/api/c/{s.cmp}/locations").json()["locations"]
    contexts = next(loc for loc in found if loc["id"] == location)["contexts"]
    return str(next(c["id"] for c in contexts if c["type"] == "warehouse"))


@pytest.fixture
def stock(owner: Client, s: Scenario) -> dict[str, Any]:
    ctx = _warehouse(owner, s, s.location)
    oil = item(owner, s.cmp, "Oil 10W-40", "liter")
    filters = item(owner, s.cmp, "Oil filter", "piece")
    receive_stock(
        owner,
        s.cmp,
        ctx,
        [
            {"item_id": oil["id"], "quantity": "10", "unit_cost": "150"},
            {"item_id": filters["id"], "quantity": "3", "unit_cost": "200"},
        ],
    )
    for item_id, amount in ((oil["id"], "250"), (filters["id"], "400")):
        owner.put(
            f"/api/c/{s.cmp}/warehouse/items/{item_id}/price",
            data={"amount": amount},
            content_type="application/json",
            headers=key(),
        )
    lot = str(LotStock.objects.get(lot__item_id=oil["id"]).lot_id)
    units = [
        str(a)
        for a in Asset.objects.filter(item_id=filters["id"])
        .order_by("created_at", "id")
        .values_list("id", flat=True)
    ]
    return {"ctx": ctx, "oil": oil["id"], "filter": filters["id"], "lot": lot, "units": units}


def _add(
    client: Client, s: Scenario, job: dict[str, Any], stock: dict[str, Any], **body: Any
) -> Any:
    data = {"expected_version": job["version"], "context_id": stock["ctx"], **body}
    return post(client, f"/api/c/{s.cmp}/service/jobs/{job['id']}/materials", data, key())


def _material(client: Client, s: Scenario, m: dict[str, Any], action: str, **body: Any) -> Any:
    data = {"expected_version": m["version"], **body}
    return post(client, f"/api/c/{s.cmp}/service/materials/{m['id']}/{action}", data, key())


def _oil_left(stock: dict[str, Any]) -> Decimal:
    return LotStock.objects.get(lot_id=stock["lot"]).quantity


def _accepted(owner: Client, s: Scenario, at: float = -6) -> dict[str, Any]:
    work = _work(owner, s, price="300")
    op = _op(owner, s, [work["id"]])
    return _ok(_do(owner, s, op, "accept", accepted_at=_at(at)))


# --- Planned materials and reserves (§ 11.7.7) ----------------------------------------------------


def test_a_planned_material_is_reserved_automatically_and_issued_later(
    owner: Client, s: Scenario, stock: dict[str, Any]
) -> None:
    op = _accepted(owner, s)
    added = _add(
        owner,
        s,
        op["jobs"][0],
        stock,
        plan=True,
        item_id=stock["oil"],
        lot_id=stock["lot"],
        quantity="4",
    )
    assert added.status_code == 201, added.content
    op = added.json()["operation"]
    material = op["materials"][0]
    # Added to the work and reserved, not issued: the stock is still there (§ 16.5.1).
    assert (material["state"], material["reserve_state"]) == ("planned", "active")
    assert _oil_left(stock) == Decimal(10)
    reserve = StockReserve.objects.get(pk=material["reserve_id"])
    assert (reserve.purpose, str(reserve.source_id)) == ("work", op["jobs"][0]["id"])
    # Not beyond the free stock (§ 11.8.3): 6 l are left free.
    beyond = _add(
        owner,
        s,
        op["jobs"][0],
        stock,
        plan=True,
        item_id=stock["oil"],
        lot_id=stock["lot"],
        quantity="7",
    )
    assert _code(beyond) == "STOCK_NOT_ENOUGH"

    op = _ok(_job(owner, s, op["jobs"][0], "start", actual_start=_at(-5)))
    # PROPOSAL: «done» waits until the planned material is issued or taken off.
    refused = _job(owner, s, op["jobs"][0], "complete", actual_end=_at(-4), odometer="10500")
    assert _code(refused) == "SERVICE_MATERIALS_NOT_ISSUED"

    op = _ok(_material(owner, s, op["materials"][0], "issue"))
    material = op["materials"][0]
    assert material["state"] == "issued" and material["reserve_state"] == "used"
    assert _oil_left(stock) == Decimal(6)
    op = _ok(_job(owner, s, op["jobs"][0], "complete", actual_end=_at(-4), odometer="10500"))
    material = op["materials"][0]
    assert material["consumed"] == "4" and material["cost"] == "600" and material["cost_known"]


def test_the_manual_mode_and_taking_a_material_off(
    owner: Client, s: Scenario, stock: dict[str, Any]
) -> None:
    owner.put(
        f"/api/c/{s.cmp}/warehouse/settings",
        data={"work_reserve_mode": "manual", "expected_version": 1},
        content_type="application/json",
        headers=key(),
    )
    op = _accepted(owner, s)
    op = _add(
        owner,
        s,
        op["jobs"][0],
        stock,
        plan=True,
        item_id=stock["filter"],
        asset_ids=stock["units"][:2],
    ).json()["operation"]
    # One planned material per unit; the manual mode reserves nothing by itself.
    assert [m["reserve_id"] for m in op["materials"]] == [None, None]
    op = _ok(_material(owner, s, op["materials"][0], "reserve"))
    first = op["materials"][0]
    assert first["reserve_state"] == "active"
    assert _code(_material(owner, s, first, "reserve")) == "SERVICE_MATERIAL_RESERVED"

    op = _ok(_material(owner, s, first, "take-off", reason="Not needed"))
    assert op["materials"][0]["state"] == "removed"
    assert StockReserve.objects.get(pk=first["reserve_id"]).state == "released"

    # A cancelled work releases what was reserved for it.
    op = _ok(_material(owner, s, op["materials"][1], "reserve"))
    reserve_id = op["materials"][1]["reserve_id"]
    op = _ok(_job(owner, s, op["jobs"][0], "cancel", reason="Client refused"))
    assert StockReserve.objects.get(pk=reserve_id).state == "released"
    assert op["materials"][1]["state"] == "removed"


# --- Parts on the vehicle (§ 11.4.3, § 11.4.7, § 11.4.8) ------------------------------------------


def _installed(
    owner: Client, s: Scenario, stock: dict[str, Any], unit: str, at: float, odometer: str
) -> dict[str, Any]:
    op = _accepted(owner, s, at - 2)
    op = _add(owner, s, op["jobs"][0], stock, item_id=stock["filter"], asset_ids=[unit]).json()[
        "operation"
    ]
    op = _ok(_job(owner, s, op["jobs"][0], "start", actual_start=_at(at - 1)))
    material = op["materials"][0]
    installed = _material(owner, s, material, "install", installed_at=_at(at), odometer=odometer)
    return _ok(installed)


def test_a_part_is_installed_replaced_removed_and_valued(
    owner: Client, s: Scenario, stock: dict[str, Any]
) -> None:
    a, b, c = stock["units"]
    op = _installed(owner, s, stock, a, at=-30, odometer="10000")
    first = op["installations"][0]
    assert first["asset"]["id"] == a and first["installed_odometer"] == "10000.0"
    assert Asset.objects.get(pk=a).state == "installed"
    # The installation consumed the issued part: its cost goes to the vehicle (§ 11.4.7).
    assert op["materials"][0]["consumed"] == "1" and op["materials"][0]["cost"] == "200"
    # § 11.4.8: the same part is not installed twice.
    again = _material(owner, s, op["materials"][0], "install", installed_at=_at(-29))
    assert _code(again) == "PART_ALREADY_INSTALLED"
    op = _ok(_job(owner, s, op["jobs"][0], "complete", actual_end=_at(-29), odometer="10050"))
    _ok(_do(owner, s, op, "finish"))

    # A second service replaces it: «done» removes the old part and installs the new one.
    work = _work(owner, s, name="Filter replacement")
    second = _ok(_do(owner, s, _op(owner, s, [work["id"]]), "accept", accepted_at=_at(-10)))
    assert [p["id"] for p in second["installed_parts"]] == [first["id"]]
    second = _add(
        owner,
        s,
        second["jobs"][0],
        stock,
        item_id=stock["filter"],
        asset_ids=[b],
        replaces_id=first["id"],
    ).json()["operation"]
    assert second["materials"][0]["replaces_id"] == first["id"]
    second = _ok(_job(owner, s, second["jobs"][0], "start", actual_start=_at(-9)))
    second = _ok(
        _job(owner, s, second["jobs"][0], "complete", actual_end=_at(-8), odometer="12000")
    )
    old = PartInstallation.objects.get(pk=first["id"])
    assert (old.removed_odometer, str(old.removal_job_id)) == (
        Decimal("12000.0"),
        second["jobs"][0]["id"],
    )
    assert (
        Asset.objects.get(pk=a).state == "removed" and Asset.objects.get(pk=b).state == "installed"
    )
    periods = owner.get(f"/api/c/{s.cmp}/warehouse/vehicles/{s.vehicle}/parts").json()
    by_asset = {p["asset"]["id"]: p for p in periods["installations"]}
    # The run of the removed part: the end values; of the installed one: to the last reading.
    assert by_asset[a]["distance"] == "2000.0" and by_asset[a]["days"] == 0
    assert by_asset[b]["distance"] == "0.0"

    # The removed part comes into the warehouse with an explicit zero (§ 11.4.7).
    awaiting = owner.get(f"/api/c/{s.cmp}/warehouse/removed-parts").json()["installations"]
    assert [p["id"] for p in awaiting] == [first["id"]]
    url = f"/api/c/{s.cmp}/warehouse/removed-parts/{first['id']}/accept"
    body = {"context_id": stock["ctx"], "cost_mode": "zero", "cost": "5"}
    assert _code(post(owner, url, body, key())) == "FIELD_NOT_APPLICABLE"
    accepted = post(owner, url, {**body, "cost": None}, key())
    assert accepted.status_code == 200, accepted.content
    unit = Asset.objects.get(pk=a)
    assert (unit.state, unit.purchase_cost) == ("in_stock", Decimal(0))
    # The removal lowers no cost counted before: the installation still cost 200.
    costs = owner.get(f"/api/c/{s.cmp}/warehouse/vehicles/{s.vehicle}/material-costs").json()
    assert [line["cost"] for line in costs["lines"]] == ["200.00", "200.00"]
    assert costs["incomplete"] is False

    # The same part goes on again: the same AST ID, a new period; the old one stays.
    _installed(owner, s, stock, a, at=-5, odometer="12100")
    assert PartInstallation.objects.filter(asset_id=a).count() == 2
    assert c  # the third unit stays in stock


def test_a_removal_checks_the_chronology_and_a_removed_part_is_written_off(
    owner: Client, s: Scenario, stock: dict[str, Any]
) -> None:
    op = _installed(owner, s, stock, stock["units"][0], at=-30, odometer="10000")
    installation = op["installations"][0]
    job = op["jobs"][0]
    url = f"/api/c/{s.cmp}/service/jobs/{job['id']}/remove-part"

    def remove(**body: Any) -> Any:
        data = {"expected_version": job["version"], "installation_id": installation["id"], **body}
        return post(owner, url, data, key())

    assert _code(remove(removed_at=_at(-31))) == "PART_REMOVAL_CHRONOLOGY"
    assert _code(remove(removed_at=_at(-20), odometer="9000")) == "PART_REMOVAL_CHRONOLOGY"
    # A missed removal is entered with its actual moment and odometer.
    removed = remove(removed_at=_at(-20), odometer="10800", comment="Found removed")
    assert removed.status_code == 200, removed.content
    reasons = owner.get(f"/api/c/{s.cmp}/warehouse/reasons").json()["reasons"]
    damage = next(r["id"] for r in reasons if r["code"] == "damage")
    written = post(
        owner,
        f"/api/c/{s.cmp}/warehouse/removed-parts/{installation['id']}/write-off",
        {"reason_id": damage},
        key(),
    )
    assert written.status_code == 200, written.content
    assert Asset.objects.get(pk=stock["units"][0]).state == "written_off"
    assert written.json()["installation"]["disposition"] == "written_off"
    # Decided once.
    again = post(
        owner,
        f"/api/c/{s.cmp}/warehouse/removed-parts/{installation['id']}/accept",
        {"context_id": stock["ctx"], "cost_mode": "unknown"},
        key(),
    )
    assert _code(again) == "PART_NOT_AWAITING_DECISION"


# --- Costs, return into another warehouse, corrections, contractor's materials ----------------


def test_costs_are_incomplete_until_the_cost_is_given(
    owner: Client, s: Scenario, stock: dict[str, Any]
) -> None:
    grease = item(owner, s.cmp, "Grease", "kilogram")
    receipt = receive_stock(
        owner, s.cmp, stock["ctx"], [{"item_id": grease["id"], "quantity": "2"}]
    )
    lot = str(LotStock.objects.get(lot__item_id=grease["id"]).lot_id)
    op = _accepted(owner, s)
    op = _add(
        owner, s, op["jobs"][0], stock, item_id=grease["id"], lot_id=lot, quantity="1"
    ).json()["operation"]
    op = _ok(_material(owner, s, op["materials"][0], "consume", quantity="1"))
    # § 11.2.9: the quantity counts in full; the costs are incomplete until the cost is entered.
    assert op["materials"][0]["cost_known"] is False
    costs = owner.get(f"/api/c/{s.cmp}/warehouse/vehicles/{s.vehicle}/material-costs").json()
    assert costs["incomplete"] is True
    line = receipt["lines"][0]
    post(
        owner,
        f"/api/c/{s.cmp}/warehouse/receipts/{receipt['id']}/lines/{line['id']}/cost",
        {"unit_cost": "80"},
        key(),
    )
    costs = owner.get(f"/api/c/{s.cmp}/warehouse/vehicles/{s.vehicle}/material-costs").json()
    assert costs["incomplete"] is False and costs["total"] == "80.00"


def test_a_work_material_returns_into_another_warehouse_and_is_not_corrected_in_the_warehouse(
    owner: Client, s: Scenario, stock: dict[str, Any]
) -> None:
    other_location = post(owner, f"/api/c/{s.cmp}/locations", {"name": "Krabi"}, key()).json()[
        "location"
    ]["id"]
    other = _warehouse(owner, s, other_location)
    op = _accepted(owner, s)
    op = _add(
        owner, s, op["jobs"][0], stock, item_id=stock["oil"], lot_id=stock["lot"], quantity="3"
    ).json()["operation"]
    material = op["materials"][0]
    warehouse_ops = owner.get(f"/api/c/{s.cmp}/warehouse/operations?kind=issue").json()
    issue = warehouse_ops["operations"][0]
    corrected = post(
        owner,
        f"/api/c/{s.cmp}/warehouse/operations/{issue['id']}/lines/{issue['lines'][0]['id']}/correct",
        {"quantity": "1", "reason": "x"},
        key(),
    )
    assert _code(corrected) == "CORRECTION_IN_SERVICE_WORK"
    op = _ok(_material(owner, s, material, "return", quantity="2", target_context_id=other))
    assert op["materials"][0]["returned"] == "2"
    child = LotStock.objects.get(lot__context_id=UUID(other))
    assert child.quantity == Decimal(2) and _oil_left(stock) == Decimal(7)


def test_materials_of_an_external_service_from_its_own_stock(
    owner: Client, s: Scenario, stock: dict[str, Any]
) -> None:
    garage = post(
        owner,
        f"/api/c/{s.cmp}/counterparties",
        {
            "kind": "company",
            "company_name": "Patong Gearbox",
            "contacts": [{"kind": "phone", "value": "+66 81 234 5678"}],
        },
        key(),
    ).json()["counterparty"]["id"]
    internal = _accepted(owner, s)
    url = f"/api/c/{s.cmp}/service/jobs/{internal['jobs'][0]['id']}/own-materials"
    body = {
        "expected_version": internal["jobs"][0]["version"],
        "name": "Gearbox oil",
        "quantity": "2.5",
        "unit": "liter",
        "cost": "900",
    }
    assert _code(post(owner, url, body, key())) == "SERVICE_NOT_EXTERNAL"

    work = _work(owner, s, name="Gearbox repair", price="8000")
    op = _op(owner, s, [work["id"]], location_id=None, counterparty_id=garage)
    job = op["jobs"][0]
    url = f"/api/c/{s.cmp}/service/jobs/{job['id']}/own-materials"
    added = post(owner, url, {**body, "expected_version": job["version"]}, key())
    assert added.status_code == 201, added.content
    own = added.json()["operation"]["own_materials"][0]
    assert (own["name"], own["quantity"], own["cost"]) == ("Gearbox oil", "2.5", "900")
    # No fictitious receipt: the warehouse has no such item or stock.
    assert not LotStock.objects.filter(lot__item__name="Gearbox oil").exists()
    unknown = post(
        owner,
        url,
        {**body, "name": "Seal", "cost": None, "expected_version": job["version"]},
        key(),
    )
    assert unknown.json()["operation"]["own_materials"][1]["cost"] is None
    removed = post(
        owner,
        f"/api/c/{s.cmp}/service/own-materials/{own['id']}/remove",
        {"expected_version": own["version"], "reason": "Entered twice"},
        key(),
    )
    assert removed.status_code == 200, removed.content
    assert removed.json()["operation"]["own_materials"][0]["removed_at"] is not None
