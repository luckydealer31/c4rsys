"""Service С4: a service at an external contractor, works of a contractor next to the company's,
the contractor's cost apart from the client's price, handover and return (ТЗ § 16.6)."""

from decimal import Decimal

import pytest
from django.test import Client

from c4rsys.companies.models import Company
from c4rsys.finance import settlements
from tests.accounts.helpers import post
from tests.companies.helpers import hire, key
from tests.rentals.scenario import NOW, Scenario, issue
from tests.service.test_service import _at, _code, _do, _job, _ok, _op, _operation, _order, _work

pytestmark = pytest.mark.django_db


@pytest.fixture
def garage(owner: Client, s: Scenario) -> str:
    body = {
        "kind": "company",
        "company_name": "Patong Gearbox",
        "contacts": [{"kind": "phone", "value": "+66 81 234 5678"}],
    }
    created = post(owner, f"/api/c/{s.cmp}/counterparties", body, key())
    assert created.status_code == 201, created.content
    garage_id: str = created.json()["counterparty"]["id"]
    return garage_id


def _owed(s: Scenario, garage: str) -> dict[str, Decimal]:
    company = Company.objects.get(public_id=s.cmp)
    return settlements.payable(company.id, garage).amounts  # type: ignore[arg-type]


def test_service_only_at_a_contractor(owner: Client, s: Scenario, garage: str) -> None:
    gearbox = _work(owner, s, name="Gearbox repair", price="8000")
    op = _op(owner, s, [gearbox["id"]], location_id=None, counterparty_id=garage)
    assert op["location_id"] is None and op["counterparty_name"] == "Patong Gearbox"
    assert op["jobs"][0]["contractor_id"] == garage
    # No own acceptance: the vehicle is handed over (§ 16.6.1).
    assert _code(_do(owner, s, op, "accept", accepted_at=_at(-5))) == "SERVICE_EXTERNAL"

    op = _ok(_do(owner, s, op, "handovers", kind="vehicle", handed_at=_at(-5)))
    assert op["state"] == "accepted" and op["handovers"][0]["returned_at"] is None
    # The vehicle at the contractor is held for rentals (decision С-3).
    assert _code(issue(owner, s, actual_start=NOW.isoformat())) == "VEHICLE_NOT_AVAILABLE"

    op = _ok(_job(owner, s, op["jobs"][0], "start", actual_start=_at(-4)))
    # Accepted by an employee of the company; the odometer may be unknown (§ 16.6.8).
    op = _ok(_job(owner, s, op["jobs"][0], "complete", actual_end=_at(-2), contractor_cost="5000"))
    assert op["jobs"][0]["state"] == "done" and op["jobs"][0]["contractor_cost"] == "5000"
    # The contractor's cost is owed to them, the client's price is charged (§ 16.6.3).
    assert _owed(s, garage) == {"THB": Decimal("5000")}
    assert _order(owner, s, op["order_id"])["amounts"]["THB"]["charged"] == "8000.00"

    op = _ok(_do(owner, s, op, "finish"))
    # The work done does not return the vehicle by itself (§ 16.6.6) and does not block closing.
    assert op["handovers"][0]["returned_at"] is None
    assert _order(owner, s, op["order_id"])["closing_conditions"] == []
    returned = post(
        owner,
        f"/api/c/{s.cmp}/service/handovers/{op['handovers'][0]['id']}/return",
        {"expected_version": op["handovers"][0]["version"], "returned_at": _at(-1)},
        key(),
    )
    assert returned.status_code == 200, returned.content
    assert returned.json()["operation"]["handovers"][0]["returned_at"] is not None


def test_contractor_work_next_to_own_works(owner: Client, s: Scenario, garage: str) -> None:
    diag = _work(owner, s, name="Diagnostics", price="500")
    repair = _work(owner, s, name="Injector repair", price="3000")
    op = _op(owner, s, [diag["id"]])
    op = _ok(_do(owner, s, op, "jobs", work_id=repair["id"], contractor_id=garage))
    contractor_job = op["jobs"][1]
    assert contractor_job["contractor_name"] == "Patong Gearbox"
    # A contractor's work has no internal executor (§ 16.6.2).
    assert _code(_job(owner, s, contractor_job, "take")) == "SERVICE_JOB_CONTRACTOR"

    # A part of the vehicle goes to the contractor; the vehicle stays.
    op = _ok(
        _do(
            owner,
            s,
            op,
            "handovers",
            kind="part",
            description="Injectors ×4",
            counterparty_id=garage,
            handed_at=_at(-5),
        )
    )
    assert op["state"] == "planned"
    op = _ok(_job(owner, s, op["jobs"][1], "start", actual_start=_at(-4)))
    assert op["state"] == "in_progress"
    op = _ok(_job(owner, s, op["jobs"][1], "complete", actual_end=_at(-3)))
    assert _owed(s, garage) == {}
    op = _ok(_job(owner, s, op["jobs"][1], "contractor-cost", contractor_cost="1800"))
    assert _owed(s, garage) == {"THB": Decimal("1800")}


def test_external_needs_its_right(owner: Client, s: Scenario, garage: str) -> None:
    gearbox = _work(owner, s, name="Gearbox repair", price="8000")
    clerk, _ = hire(
        owner,
        s.cmp,
        "clerk@example.com",
        [{"type": "grant", "permission": p} for p in ("service.view", "service.create")],
    )
    refused = _operation(clerk, s, [gearbox["id"]], location_id=None, counterparty_id=garage)
    assert refused.status_code == 403
    assert _code(_operation(owner, s, [gearbox["id"]], location_id=None)) == "FIELD_REQUIRED"
