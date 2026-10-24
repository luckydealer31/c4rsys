"""Warehouse, part С1: storage of contexts, catalog, packagings, barcodes, rights (ТЗ § 11.1,
§ 11.8.4–11.8.5, § 23.2.6, § 23.7.3)."""

from typing import Any

import pytest
from django.test import Client

from c4rsys.companies.models import ActivityContext, PermissionSet
from c4rsys.platform.models import AuditEvent
from c4rsys.warehouse.models import Item
from tests.accounts.helpers import post
from tests.companies.helpers import create_company, hire, key

pytestmark = pytest.mark.django_db


@pytest.fixture
def cmp(owner: Client) -> str:
    public_id: str = create_company(owner, timezone="Asia/Bangkok").json()["company"]["public_id"]
    return public_id


def location(owner: Client, cmp: str, *contexts: str) -> dict[str, Any]:
    body = {"name": "Phuket", "contexts": list(contexts)}
    response = post(owner, f"/api/c/{cmp}/locations", body, key())
    assert response.status_code == 201, response.content
    created: dict[str, Any] = response.json()["location"]
    return created


def item(owner: Client, cmp: str, **data: Any) -> Any:
    return post(owner, f"/api/c/{cmp}/warehouse/items", {"name": "Oil 5W-30", **data}, key())


# --- Storage of contexts (§ 11.1.5–11.1.7) -----------------------------------------------------


def test_warehouse_always_stores_rental_and_service_by_setting(owner: Client, cmp: str) -> None:
    created = location(owner, cmp, "rental", "service", "warehouse")
    by_type = {c["type"]: c for c in created["contexts"]}
    assert by_type["warehouse"]["storage_enabled"] is True
    assert by_type["rental"]["storage_enabled"] is False
    assert by_type["service"]["storage_enabled"] is False

    base = f"/api/c/{cmp}/locations/{created['id']}/contexts"
    on = post(owner, f"{base}/{by_type['service']['id']}/storage", {"enabled": True}, key())
    assert on.status_code == 200, on.content
    always = post(owner, f"{base}/{by_type['warehouse']['id']}/storage", {"enabled": False}, key())
    assert always.json()["error"]["code"] == "STORAGE_ALWAYS_ON"

    storages = owner.get(f"/api/c/{cmp}/warehouse/storages").json()["storages"]
    assert sorted(s["type"] for s in storages) == ["service", "warehouse"]


def test_warehouse_context_without_storage_is_refused_by_the_database(
    owner: Client, cmp: str
) -> None:
    created = location(owner, cmp, "warehouse")
    with pytest.raises(Exception):  # noqa: B017 — the CHECK constraint
        ActivityContext.objects.filter(id=created["contexts"][0]["id"]).update(
            storage_enabled=False
        )


# --- Catalog (§ 11.1.11–11.1.13) ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("unit", "accounting"),
    [("piece", "individual"), ("instance", "individual"), ("liter", "batch"), ("meter", "batch")],
)
def test_accounting_follows_the_unit(owner: Client, cmp: str, unit: str, accounting: str) -> None:
    response = item(owner, cmp, unit=unit)

    assert response.status_code == 201, response.content
    assert response.json()["item"]["accounting"] == accounting


def test_unit_is_immutable_and_other_data_change(owner: Client, cmp: str) -> None:
    created = item(owner, cmp, unit="liter").json()["item"]
    url = f"/api/c/{cmp}/warehouse/items/{created['id']}"

    refused = owner.patch(
        url,
        data={"unit": "kilogram", "name": "X", "expected_version": 1},
        content_type="application/json",
        headers=key(),
    )
    assert refused.json()["error"]["code"] == "ITEM_UNIT_IMMUTABLE"

    changed = owner.patch(
        url,
        data={
            "name": "Shell Helix 5W-30",
            "manufacturer_other": "Shell",
            "originality": "original",
            "side": "both",
            "applicability": [{"make": "TOYOTA"}, {"make": "TOYOTA", "model": "TOYOTA_YARIS"}],
            "expected_version": 1,
        },
        content_type="application/json",
        headers=key(),
    )
    assert changed.status_code == 200, changed.content
    body = changed.json()["item"]
    assert body["unit"] == "liter"
    assert body["manufacturer_name"] == "Shell"
    assert len(body["applicability"]) == 2
    event = AuditEvent.objects.get(action="warehouse.item_changed")
    assert "unit" not in (event.after or {})


def test_unused_item_is_deleted_or_archived(owner: Client, cmp: str) -> None:
    first = item(owner, cmp, unit="piece").json()["item"]
    second = item(owner, cmp, unit="piece", name="Wiper").json()["item"]

    deleted = owner.delete(f"/api/c/{cmp}/warehouse/items/{first['id']}", headers=key())
    archived = post(
        owner,
        f"/api/c/{cmp}/warehouse/items/{second['id']}/archive",
        {"expected_version": 1},
        key(),
    )

    assert deleted.status_code == 200
    assert archived.json()["item"]["state"] == "archived"
    assert not Item.objects.filter(pk=first["id"]).exists()
    active = owner.get(f"/api/c/{cmp}/warehouse/items").json()["items"]
    assert active == []


# --- Packagings and barcodes (§ 11.1.15–11.1.16) ------------------------------------------------


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("1.005", "QUANTITY_TOO_PRECISE"),
        ("0", "QUANTITY_NOT_POSITIVE"),
        ("abc", "VALIDATION_ERROR"),
    ],
)
def test_packaging_quantity_to_hundredths(owner: Client, cmp: str, value: str, code: str) -> None:
    oil = item(owner, cmp, unit="liter").json()["item"]

    response = post(
        owner, f"/api/c/{cmp}/warehouse/items/{oil['id']}/packagings", {"quantity": value}, key()
    )

    assert response.json()["error"]["code"] == code


def test_packagings_only_for_materials_and_barcode_finds_them(owner: Client, cmp: str) -> None:
    oil = item(owner, cmp, unit="liter").json()["item"]
    with_pack = post(
        owner,
        f"/api/c/{cmp}/warehouse/items/{oil['id']}/packagings",
        {"quantity": "4.00", "name": "Канистра"},
        key(),
    ).json()["item"]
    packaging = with_pack["packagings"][0]["id"]
    post(
        owner,
        f"/api/c/{cmp}/warehouse/items/{oil['id']}/barcodes",
        {"code": "4607000000001", "packaging_id": packaging},
        key(),
    )

    found = owner.get(f"/api/c/{cmp}/warehouse/barcodes/4607000000001").json()
    assert found["item"]["id"] == oil["id"] and found["packaging_id"] == packaging
    taken = post(
        owner,
        f"/api/c/{cmp}/warehouse/items/{oil['id']}/barcodes",
        {"code": "4607000000001"},
        key(),
    )
    assert taken.json()["error"]["code"] == "BARCODE_TAKEN"
    wiper = item(owner, cmp, unit="piece", name="Wiper").json()["item"]
    refused = post(
        owner, f"/api/c/{cmp}/warehouse/items/{wiper['id']}/packagings", {"quantity": "1"}, key()
    )
    assert refused.json()["error"]["code"] == "PACKAGING_FOR_MATERIALS_ONLY"


# --- Rights (§ 23.2.6, § 23.7.3) -----------------------------------------------------------------


def test_storekeeper_set_and_rights(owner: Client, cmp: str) -> None:
    storekeeper = PermissionSet.objects.get(code="storekeeper", is_system=True)
    codes = {str(c) for c in storekeeper.permissions.values_list("code", flat=True)}
    assert "warehouse.receive" in codes and "warehouse.adjust.confirm" not in codes
    assert "warehouse.purchase" not in codes and "warehouse.sell" not in codes

    rights = [
        {
            "type": "grant",
            "permission": "warehouse.view",
            "locations": "all",
            "contexts": "all",
            "cash_accounts": "all",
        }
    ]
    viewer, _ = hire(owner, cmp, "viewer@example.com", rights)
    assert viewer.get(f"/api/c/{cmp}/warehouse/items").status_code == 200
    assert item(viewer, cmp, unit="piece").status_code == 403
