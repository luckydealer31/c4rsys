"""Block 15: the system dictionaries of the warehouse catalog kept by the platform administrator
(ТЗ § 11.1.12–11.1.14): manufacturers, the linking of «Other» values with the history, aliases
for search; system attributes of parts with their values, on items and in the filter."""

from typing import Any

import pytest
from django.test import Client

from c4rsys.backoffice.models import SystemJournalEntry
from c4rsys.platform.models import AuditEvent
from c4rsys.warehouse.models import Item, Manufacturer
from tests.accounts.helpers import post
from tests.backoffice.helpers import Member, err, sp
from tests.companies.helpers import key
from tests.rentals.scenario import Scenario

pytestmark = pytest.mark.django_db


def _item(owner: Client, cmp: str, **body: Any) -> Any:
    return post(owner, f"/api/c/{cmp}/warehouse/items", {"unit": "piece", **body}, key())


def _patch(m: Member, path: str, body: dict[str, Any]) -> Any:
    return m.client.patch(
        f"/api/staff/{path}", data=body, content_type="application/json", headers=key()
    )


def test_only_the_platform_administrator_keeps_the_dictionaries(
    admin: Member, support: Member
) -> None:
    assert support.client.get("/api/staff/warehouse/manufacturers").status_code == 403
    assert admin.client.get("/api/staff/warehouse/manufacturers").status_code == 200


def test_other_values_are_linked_to_a_manufacturer_with_the_history(
    owner: Client, s: Scenario, admin: Member
) -> None:
    first = _item(owner, s.cmp, name="Brake pads", manufacturer_other="Brembo").json()["item"]
    _item(owner, s.cmp, name="Brake disc", manufacturer_other="brembo")
    _item(owner, s.cmp, name="Spark plug", manufacturer_other="NGK")
    groups = admin.client.get("/api/staff/warehouse/manufacturer-other").json()["other"]
    brembo = next(g for g in groups if g["text"].lower() == "brembo")
    # Spellings in another letter case are one group.
    assert (brembo["items"], brembo["companies"], len(brembo["variants"])) == (2, 1, 2)

    made = sp(
        admin,
        "warehouse/manufacturers",
        {"name": "Brembo", "aliases": ["Брембо"], "reason": "Common brand"},
    )
    assert made.status_code == 201, made.content
    manufacturer = made.json()["manufacturer"]
    assert err(sp(admin, "warehouse/manufacturers", {"name": "brembo", "reason": "x"})) == (
        "MANUFACTURER_EXISTS"
    )
    linked = sp(
        admin,
        "warehouse/manufacturer-link",
        {"texts": brembo["variants"], "manufacturer_id": manufacturer["id"], "reason": "Same"},
    )
    assert linked.status_code == 200, linked.content
    assert linked.json()["linked"] == 2
    stored = Item.objects.get(pk=first["id"])
    assert (str(stored.manufacturer_id), stored.manufacturer_other) == (manufacturer["id"], "")
    # The history stays: in the company's journal and in the system journal.
    entry = AuditEvent.objects.filter(
        action="warehouse.item_changed", object_id=first["id"]
    ).latest("occurred_at")
    assert entry.before == {"manufacturer": None, "manufacturer_other": "Brembo"}
    assert entry.company_id == stored.company_id
    assert SystemJournalEntry.objects.filter(action="warehouse.manufacturer_linked").count() == 2
    # The spellings are aliases for search, not new manufacturers.
    assert Manufacturer.objects.count() == 1
    found = owner.get("/api/catalog/manufacturers?q=брембо").json()["manufacturers"]
    assert [m["name"] for m in found] == ["Brembo"]
    remaining = admin.client.get("/api/staff/warehouse/manufacturer-other").json()["other"]
    assert [g["text"] for g in remaining] == ["NGK"]


def test_system_attributes_are_added_and_used_on_items(
    owner: Client, s: Scenario, admin: Member
) -> None:
    names = {"ru": "Тип резьбы", "en": "Thread type", "th": "ชนิดเกลียว"}
    body = {
        "code": "thread",
        "name": names,
        "values": [
            {"code": "metric", "name": {"ru": "Метрическая", "en": "Metric", "th": "เมตริก"}},
            {"code": "inch", "name": {"ru": "Дюймовая", "en": "Inch", "th": "นิ้ว"}},
        ],
        "reason": "Requested by companies",
    }
    made = sp(admin, "warehouse/attributes", body)
    assert made.status_code == 201, made.content
    attribute = made.json()["attribute"]
    # A base attribute of § 11.1.13 is not added again.
    assert err(sp(admin, "warehouse/attributes", {**body, "code": "side"})) == "ATTRIBUTE_EXISTS"

    options = owner.get("/api/catalog/warehouse-options").json()["extra_attributes"]
    assert [o["code"] for o in options] == ["thread"]
    bolt = _item(owner, s.cmp, name="Bolt", extra_attributes={"thread": ["metric"]})
    assert bolt.status_code == 201, bolt.content
    assert bolt.json()["item"]["extra_attributes"] == {"thread": ["metric"]}
    # One value unless the attribute allows several; unknown values are refused.
    two = _item(owner, s.cmp, name="Nut", extra_attributes={"thread": ["metric", "inch"]})
    assert err(two) == "ATTRIBUTE_ONE_VALUE"
    assert err(_item(owner, s.cmp, name="Nut", extra_attributes={"thread": ["x"]})) == (
        "ATTRIBUTE_VALUE_UNKNOWN"
    )
    # No value — «Not given».
    plain = _item(owner, s.cmp, name="Washer").json()["item"]
    assert plain["extra_attributes"] == {}
    filtered = owner.get(f"/api/c/{s.cmp}/warehouse/items?attr=thread:metric").json()["items"]
    assert [i["name"] for i in filtered] == ["Bolt"]

    changed = _patch(
        admin,
        f"warehouse/attributes/{attribute['id']}",
        {
            "multiple": True,
            "add_values": [{"code": "pipe", "name": {"ru": "Трубная", "en": "Pipe", "th": "ท่อ"}}],
            "reason": "More values",
        },
    )
    assert changed.status_code == 200, changed.content
    assert [v["code"] for v in changed.json()["attribute"]["values"]] == ["metric", "inch", "pipe"]
    both = _item(owner, s.cmp, name="Adapter", extra_attributes={"thread": ["metric", "pipe"]})
    assert both.status_code == 201, both.content
    back = _patch(
        admin, f"warehouse/attributes/{attribute['id']}", {"multiple": False, "reason": "x"}
    )
    assert err(back) == "ATTRIBUTE_MULTIPLE_ONLY_ON"
