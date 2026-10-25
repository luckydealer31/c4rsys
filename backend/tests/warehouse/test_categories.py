"""Categories and subcategories of parts: platform catalog from the customer's file, choice by
the name in three languages, the best with alternatives, items and import (decisions К1–К7 of
25.10.2026)."""

from typing import Any

import pytest
from django.test import Client

from c4rsys.warehouse.models import Item, PartCategory, PartSubcategory
from tests.accounts.helpers import post
from tests.companies.helpers import create_company, key
from tests.warehouse.test_import import upload, workbook

pytestmark = pytest.mark.django_db


@pytest.fixture
def cmp(owner: Client) -> str:
    public_id: str = create_company(owner).json()["company"]["public_id"]
    return public_id


def suggest(client: Client, name: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = client.get(
        "/api/catalog/part-categories/suggest", {"name": name}
    ).json()["suggestions"]
    return found


def code_of(subcategory_id: str) -> str:
    return str(PartSubcategory.objects.get(pk=subcategory_id).code)


def test_catalog_is_loaded_from_the_file(owner: Client) -> None:
    tree = owner.get("/api/catalog/part-categories").json()["categories"]
    assert len(tree) == 8 and sum(len(c["subcategories"]) for c in tree) == 17
    brakes = next(c for c in tree if c["code"] == "brakes")
    assert brakes["names"] == {"ru": "Тормозная система", "en": "Brake system", "th": "ระบบเบรก"}


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Колодки тормозные передние", "brakes_main"),
        ("Диск тормозной задний", "brakes_main"),
        ("Колодки мото Honda PCX", "moto_brakes"),
        ("Ремень ГРМ Toyota", "engine_timing"),
        ("Аккумулятор 60Ah", "electrics_main"),
        ("Brake pads front", "brakes_main"),
        ("Spark plug NGK", "electrics_main"),
        ("ผ้าเบรกหน้า", "brakes_main"),
        ("Диск сцепления", "transmission_clutch"),
    ],
)
def test_choice_by_name(owner: Client, name: str, expected: str) -> None:
    found = suggest(owner, name)
    assert found, name
    assert code_of(found[0]["subcategory"]) == expected


def test_nothing_found_and_alternatives(owner: Client) -> None:
    assert suggest(owner, "Масло моторное 5W-30") == []
    # К2: the best match first, the others offered.
    found = suggest(owner, "Колодки мото тормозные")
    assert [code_of(f["subcategory"]) for f in found][:2] == ["moto_brakes", "brakes_main"]


def test_item_keeps_category_or_none(owner: Client, cmp: str) -> None:
    sub = PartSubcategory.objects.get(code="brakes_main")
    other = PartCategory.objects.get(code="engine")
    url = f"/api/c/{cmp}/warehouse/items"
    created = post(
        owner, url, {"name": "Pads", "unit": "piece", "part_subcategory": str(sub.id)}, key()
    )
    assert created.status_code == 201, created.content
    item = created.json()["item"]
    assert (item["part_category"], item["part_subcategory"]) == (str(sub.category_id), str(sub.id))
    mismatch = post(
        owner,
        url,
        {
            "name": "X",
            "unit": "piece",
            "part_category": str(other.id),
            "part_subcategory": str(sub.id),
        },
        key(),
    )
    assert mismatch.json()["error"]["code"] == "SUBCATEGORY_OF_OTHER_CATEGORY"
    # К3: «without a category» stays possible.
    plain = post(owner, url, {"name": "Колодки тормозные", "unit": "piece"}, key()).json()["item"]
    assert plain["part_category"] is None
    found = owner.get(f"{url}?q=тормозная").json()["items"]
    assert [i["name"] for i in found] == ["Pads"]


def test_import_takes_the_file_first_then_the_name(owner: Client, cmp: str) -> None:
    content = workbook(
        [
            {"name": "Ремень ГРМ", "unit": "piece", "category": "Кузов"},
            {"name": "Свеча зажигания", "unit": "piece"},
            {"name": "Масло", "unit": "liter", "category": "Масла"},
        ]
    )
    operation = upload(owner, cmp, content).json()["import"]
    url = f"/api/c/{cmp}/warehouse/imports/{operation['id']}/post"
    assert post(owner, url, {"expected_version": operation["version"]}, key()).status_code == 200
    belt = Item.objects.get(name="Ремень ГРМ")
    assert belt.part_category is not None and belt.part_category.code == "body"
    plug = Item.objects.get(name="Свеча зажигания")
    assert plug.part_subcategory is not None and plug.part_subcategory.code == "electrics_main"
    oil = Item.objects.get(name="Масло")
    assert oil.part_category is None and oil.category == "Масла"
