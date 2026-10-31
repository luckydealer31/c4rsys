"""Block 16 part Б: settlement and counterparty documents (ТЗ § 18.1.1, § 18.1.6), the scope of a
document by contexts (§ 18.5.4), the context of notifications and tasks (§ 19.1.7, § 20.1.5),
reminders of repeating tasks (§ 19.2.2), missing events and automatic tasks (§ 19.1.9,
§ 20.3.4–20.3.5) and the Thai explanations of the import templates (§ 22.2.6, Б-7)."""

import io
from collections.abc import Iterator
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from django.test import Client
from django.utils import timezone
from openpyxl import load_workbook

from c4rsys.fleet.models import IdentifierConflict
from c4rsys.notifications import rules
from c4rsys.notifications import services as notifications
from c4rsys.notifications.models import Notification, NotificationRecipient
from c4rsys.rentals import issue as issue_module
from c4rsys.tasks.models import Task, TaskSeries
from c4rsys.warehouse import imports
from c4rsys.warehouse.import_help_th import TH
from tests.accounts.helpers import post
from tests.companies.helpers import create_company, hire, key
from tests.finance.test_deposits import _account
from tests.rentals.scenario import NOW, Scenario, add_location, build, issue
from tests.warehouse.test_inventory import base, count_lines, new_count
from tests.warehouse.test_operations import item as new_item
from tests.warehouse.test_operations import location as warehouse_location
from tests.warehouse.test_operations import receive_stock, warehouse_of

pytestmark = pytest.mark.django_db


@pytest.fixture
def s(owner: Client) -> Scenario:
    return build(owner)


@pytest.fixture
def frozen() -> Iterator[None]:
    with mock.patch.object(issue_module, "timezone", SimpleNamespace(now=lambda: NOW)):
        yield


def _kind(client: Client, s: Scenario, code: str) -> str:
    kinds = client.get(f"/api/c/{s.cmp}/document-kinds").json()["kinds"]
    kind_id: str = next(k["id"] for k in kinds if k["code"] == code)
    return kind_id


def _template(client: Client, s: Scenario, source: str, content: str) -> str:
    body = {
        "title": f"By {source}",
        "kind_id": _kind(client, s, "settlement" if source == "payment" else "contract"),
        "source": source,
        "content": content,
        "required_fields": [],
        "signers": {},
        "language": "en",
    }
    response = post(client, f"/api/c/{s.cmp}/document-templates", body, key())
    assert response.status_code == 201, response.content
    template_id: str = response.json()["template"]["id"]
    return template_id


def _rental_context(s: Scenario) -> str:
    from c4rsys.companies.models import ActivityContext

    return str(ActivityContext.objects.get(location_id=s.location, type="rental").id)


# --- § 18.1.6: settlement documents; § 18.1.1: documents of a counterparty ---------------------


def test_settlement_document_of_a_payment(owner: Client, s: Scenario, frozen: None) -> None:
    rental = issue(owner, s).json()["rental"]
    account = _account(owner, s)
    paid = post(
        owner,
        f"/api/c/{s.cmp}/payments/cash",
        {
            "cash_account_id": account,
            "client_id": s.client,
            "amount": "700",
            "currency": "THB",
            "allocations": [{"order_id": rental["order_id"], "amount": "700"}],
        },
        key(),
    )
    assert paid.status_code == 201, paid.content
    record = paid.json()["payment"]
    template = _template(
        owner,
        s,
        "payment",
        "# Receipt {{payment.number}}\n{{payment.kind}}: {{payment.amount}} from {{payment.payer}}"
        "\nOrders {{payment.orders}}, {{payment.order_amount}}\n{{table:payment.parts}}",
    )
    made = post(
        owner,
        f"/api/c/{s.cmp}/documents/from-template",
        {"template_id": template, "source_id": record["id"]},
        key(),
    )
    assert made.status_code == 201, made.content
    d = made.json()["document"]
    content = d["versions"][0]["content"]
    order_number = owner.get(f"/api/c/{s.cmp}/orders/{rental['order_id']}").json()["order"][
        "number"
    ]
    assert "Payment received: 700.00 THB from Petrov Ivan" in content
    assert f"Orders {order_number}, 700.00 THB" in content
    assert "<td>Payment</td>" in content
    assert {x["object_type"] for x in d["links"]} == {"client", "order"}
    # The record is read only by the employee who may see its account (PROPOSAL).
    clerk, _ = hire(
        owner,
        s.cmp,
        "clerk@example.com",
        [{"type": "grant", "permission": "documents.create"}],
    )
    hidden = post(
        clerk,
        f"/api/c/{s.cmp}/documents/preview",
        {"template_id": template, "source_id": record["id"]},
        key(),
    )
    assert hidden.status_code == 404


def test_document_of_a_counterparty(owner: Client, s: Scenario) -> None:
    card = post(
        owner,
        f"/api/c/{s.cmp}/counterparties",
        {
            "kind": "company",
            "company_name": "Garage Ltd",
            "contacts": [{"kind": "email", "value": "garage@x.example"}],
        },
        key(),
    ).json()["counterparty"]["id"]
    template = _template(
        owner, s, "counterparty", "Contract with {{counterparty.full_name}}, {{counterparty.email}}"
    )
    d = post(
        owner,
        f"/api/c/{s.cmp}/documents/from-template",
        {"template_id": template, "source_id": card},
        key(),
    ).json()["document"]
    assert "Contract with Garage Ltd, garage@x.example" in d["versions"][0]["content"]
    assert [x["object_type"] for x in d["links"]] == ["counterparty"]
    catalog = owner.get(f"/api/c/{s.cmp}/document-fields").json()
    assert "payment.parts" in {x["code"] for x in catalog["tables"]}


# --- § 18.5.4: the scope by contexts ----------------------------------------------------------


def test_document_scope_by_a_context(owner: Client, s: Scenario) -> None:
    from c4rsys.companies.models import ActivityContext
    from tests.documents.test_registry import _create

    post(owner, f"/api/c/{s.cmp}/locations/{s.location}/contexts", {"type": "service"}, key())
    rental_ctx = _rental_context(s)
    service_ctx = str(ActivityContext.objects.get(location_id=s.location, type="service").id)
    renter, _ = hire(
        owner,
        s.cmp,
        "renter@example.com",
        [
            {
                "type": "grant",
                "permission": "documents.view",
                "locations": [s.location],
                "contexts": [rental_ctx],
            }
        ],
    )
    mechanic, _ = hire(
        owner,
        s.cmp,
        "mechanic@example.com",
        [
            {
                "type": "grant",
                "permission": "documents.view",
                "locations": [s.location],
                "contexts": [service_ctx],
            }
        ],
    )
    d = _create(owner, s, locations=[], contexts=[rental_ctx]).json()["document"]
    assert not d["all_locations"] and d["contexts"] == [rental_ctx] and d["locations"] == []
    assert renter.get(f"/api/c/{s.cmp}/documents/{d['id']}").status_code == 200
    assert mechanic.get(f"/api/c/{s.cmp}/documents/{d['id']}").status_code == 404
    # Changed later: the whole location covers both contexts.
    changed = owner.patch(
        f"/api/c/{s.cmp}/documents/{d['id']}",
        data={"expected_version": d["version"], "locations": [s.location], "contexts": []},
        content_type="application/json",
        headers=key(),
    )
    assert changed.status_code == 200, changed.content
    assert changed.json()["document"]["contexts"] == []
    assert mechanic.get(f"/api/c/{s.cmp}/documents/{d['id']}").status_code == 200
    unknown = _create(owner, s, locations=[], contexts=[s.location])
    assert unknown.json()["error"]["code"] == "CONTEXT_UNKNOWN"


# --- § 20.1.5, § 19.1.7: the context of notifications and tasks --------------------------------


def test_notification_has_the_context_of_its_section(owner: Client, s: Scenario) -> None:
    rules.raise_event(
        _company_id(s),
        "rental.booking_cancelled",
        condition="test:1",
        location_id=_uuid(s.location),
        done=True,
    )
    n = Notification.objects.get(condition="test:1")
    assert str(n.context_id) == _rental_context(s)
    found = owner.get("/api/notifications?state=done&context_type=rental").json()["notifications"]
    assert [x["context"]["type"] for x in found] == ["rental"]
    assert owner.get("/api/notifications?state=done&context_type=service").json() == {
        "notifications": []
    }


def _company_id(s: Scenario) -> Any:
    from c4rsys.companies.models import Company

    return Company.objects.get(public_id=s.cmp).id


def _uuid(value: str) -> Any:
    from uuid import UUID

    return UUID(value)


def test_task_in_a_context_and_reminders_of_a_repetition(owner: Client, s: Scenario) -> None:
    rental_ctx = _rental_context(s)
    other = add_location(owner, s.cmp, "Krabi")
    tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
    refused = post(
        owner,
        f"/api/c/{s.cmp}/tasks",
        {"title": "Wash", "location_id": other, "context_id": rental_ctx},
        key(),
    )
    assert refused.json()["error"]["code"] == "CONTEXT_NOT_IN_LOCATION"
    created = post(
        owner,
        f"/api/c/{s.cmp}/tasks",
        {
            "title": "Wash",
            "location_id": s.location,
            "context_id": rental_ctx,
            "due_date": tomorrow,
            "due_time": "18:00",
            "reminders": [60],
            "repeat": {"frequency": "daily"},
        },
        key(),
    )
    assert created.status_code == 201, created.content
    task = created.json()["task"]
    assert task["context_id"] == rental_ctx
    series = TaskSeries.objects.get()
    assert series.reminders == [60] and str(series.context_id) == rental_ctx
    # § 19.2.2: «this and the following» changes the reminders of the next repetitions.
    changed = post(
        owner,
        f"/api/c/{s.cmp}/tasks/{task['id']}/repeat",
        {"action": "change", "expected_version": series.version, "reminders": [120, 30]},
        key(),
    )
    assert changed.status_code == 200, changed.content
    assert changed.json()["task"]["reminders"] == [120, 30]
    from c4rsys.tasks import series as series_module

    series_module.run(timezone.now() + timedelta(days=3))
    following = Task.objects.exclude(pk=task["id"]).order_by("series_on").first()
    assert following is not None
    assert following.reminders == [120, 30] and str(following.context_id) == rental_ctx
    listed = owner.get(f"/api/c/{s.cmp}/tasks?view=all&context={rental_ctx}").json()["tasks"]
    assert task["id"] in {x["id"] for x in listed}


# --- § 20.3.4–20.3.5, § 19.1.9: events and automatic tasks -------------------------------------


def test_adjustment_confirmation_notice_and_task(owner: Client) -> None:
    cmp = create_company(owner, timezone="Asia/Bangkok", main_currency="THB").json()["company"][
        "public_id"
    ]
    place = warehouse_location(owner, cmp, "Phuket")
    ctx = warehouse_of(place)
    pads = new_item(owner, cmp, "Pads", "piece")
    receive_stock(owner, cmp, ctx, [{"item_id": pads["id"], "quantity": 1, "unit_cost": "5"}])
    count = new_count(owner, cmp, ctx)
    count = post(owner, f"{base(cmp, count)}/items", {"all": True}, key()).json()["inventory"]
    count_lines(owner, cmp, count, {count["lines"][0]["id"]: 1})
    post(owner, f"{base(cmp, count)}/submit", {}, key())
    notifications.run_checks(timezone.now())
    n = Notification.objects.get(event="warehouse.adjustment_confirmation")
    assert n.state == "active" and str(n.context_id) == ctx and n.kind == "action"
    task = Task.objects.get(system_event="warehouse.adjustment_confirmation")
    assert task.state == "open" and str(task.context_id) == ctx
    post(owner, f"{base(cmp, count)}/confirm", {"reason": "Checked"}, key())
    notifications.run_checks(timezone.now())
    n.refresh_from_db()
    task.refresh_from_db()
    assert n.state == "done" and n.done_reason == "inventory_confirmed"
    assert task.state == "done" and task.state_note == "inventory_confirmed"


def test_identifier_conflict_is_told_to_each_company(owner: Client) -> None:
    vin = "JTDBR32E720123456"
    ids = []
    for name, k in (("Alpha", "c-a"), ("Beta", "c-b")):
        cmp = create_company(owner, k, display_name=name, timezone="Asia/Bangkok").json()[
            "company"
        ]["public_id"]
        place = post(owner, f"/api/c/{cmp}/locations", {"name": f"{name} base"}, key()).json()[
            "location"
        ]["id"]
        made = post(
            owner,
            f"/api/c/{cmp}/vehicles",
            {"name": "Yaris", "location_id": place, "odometer_unit": "km", "vin": vin},
            key(),
        )
        assert made.status_code == 201, made.content
        ids.append(cmp)
    conflict = IdentifierConflict.objects.get(is_open=True)
    notifications.run_checks(timezone.now())
    told = Notification.objects.filter(event="vehicle.identifier_conflict_opened", state="active")
    assert {n.company.public_id for n in told} == set(ids)
    assert {n.params["number"] for n in told} == {conflict.number}
    assert NotificationRecipient.objects.filter(notification__in=told).count() >= 2
    conflict.is_open = False
    conflict.closed_at = timezone.now()
    conflict.save(update_fields=["is_open", "closed_at"])
    notifications.run_checks(timezone.now())
    assert not Notification.objects.filter(
        event="vehicle.identifier_conflict_opened", state="active"
    ).exists()


# --- § 22.2.6, decision Б-7: three languages on the explanation sheet ---------------------------


@pytest.mark.parametrize("kind", ["vehicles", "clients", "counterparties", "items", "stock"])
def test_templates_explain_columns_in_thai(kind: str) -> None:
    book = load_workbook(io.BytesIO(imports.template(kind)))
    rows = list(book[imports.HELP_SHEET].iter_rows(values_only=True))
    assert rows[0] == ("code", "required", "en", "ru", "th", "example")
    adapter = imports.adapter(kind)
    for row in rows[1 : 1 + len(adapter.columns)]:
        assert row[4], row
    assert all(adapter.help[c][0] in TH for c in adapter.columns)
