"""The act of completed works as a condition of closing a client's order (ТЗ § 16.7.5–16.7.6;
customer's decision Р-9 of 29.10.2026)."""

from typing import Any

import pytest
from django.test import Client

from tests.accounts.helpers import post
from tests.companies.helpers import key
from tests.documents.test_registry import _create, _file, _kind
from tests.rentals.scenario import Scenario
from tests.service.test_service import _at, _do, _done, _ok, _op, _order, _work

pytestmark = pytest.mark.django_db


def _setting(client: Client, s: Scenario, **body: Any) -> Any:
    current = client.get(f"/api/c/{s.cmp}/service-act-setting").json()["setting"]
    return client.put(
        f"/api/c/{s.cmp}/service-act-setting",
        data={"expected_version": current["version"], **body},
        content_type="application/json",
        headers=key(),
    )


def _finished(owner: Client, s: Scenario, **body: Any) -> dict[str, Any]:
    op = _op(owner, s, [_work(owner, s)["id"]], **body)
    op = _ok(_do(owner, s, op, "accept", accepted_at=_at(-3)))
    op = _done(owner, s, op)
    return _ok(_do(owner, s, op, "finish"))


def _conditions(owner: Client, s: Scenario, op: dict[str, Any]) -> list[str]:
    return [c["condition"] for c in _order(owner, s, op["order_id"])["closing_conditions"]]


def test_act_required_and_signed_by_client(owner: Client, s: Scenario) -> None:
    default = owner.get(f"/api/c/{s.cmp}/service-act-setting").json()["setting"]
    assert default == {"act_required": False, "act_signed": False, "version": 0}
    wrong = _setting(owner, s, act_required=False, act_signed=True)
    assert wrong.json()["error"]["code"] == "SERVICE_ACT_SIGNED_NEEDS_REQUIRED"
    assert _setting(owner, s, act_required=True, act_signed=True).status_code == 200

    op = _finished(owner, s)
    assert _conditions(owner, s, op) == ["service_act_missing"]
    link = {"object_type": "service_operation", "object_id": op["id"]}
    d = _create(owner, s, kind_id=_kind(owner, s, "act"), links=[link]).json()["document"]
    assert _conditions(owner, s, op) == ["service_act_not_signed"]
    signing = post(
        owner,
        f"/api/c/{s.cmp}/documents/{d['id']}/signing",
        {"expected_version": d["version"], "signers": [{"role": "client", "id": s.client}]},
        key(),
    ).json()["signing"]
    post(
        owner,
        f"/api/c/{s.cmp}/documents/{d['id']}/signers/{signing['signers'][0]['id']}/paper",
        {"file_id": _file(owner), "signed_on": "2026-10-01"},
        key(),
    )
    assert _conditions(owner, s, op) == []


def test_internal_order_needs_no_act(owner: Client, s: Scenario) -> None:
    _setting(owner, s, act_required=True, act_signed=False)
    op = _finished(owner, s, client_id=None, internal=True)
    assert _conditions(owner, s, op) == []
