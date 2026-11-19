"""The list of the stock with the attributes of the card and what lies behind the quantity
(customer's decisions of 20.11.2026): the manufacturer and the country of manufacture, the
number of units or lots and the identifier of the only one, the range of the purchase costs."""

from typing import Any

import pytest
from django.test import Client

from tests.accounts.helpers import post
from tests.companies.helpers import key
from tests.warehouse.test_receipts import cmp, receipt, warehouse  # noqa: F401

pytestmark = pytest.mark.django_db


def _item(owner: Client, cmp: str, **body: Any) -> Any:  # noqa: F811
    return post(owner, f"/api/c/{cmp}/warehouse/items", {"unit": "piece", **body}, key())


def _posted(owner: Client, cmp: str, context: str, lines: list[dict[str, Any]]) -> None:  # noqa: F811
    made = receipt(owner, cmp, context_id=context, lines=lines)
    assert made.status_code == 201, made.content
    body = made.json()["receipt"]
    done = post(
        owner,
        f"/api/c/{cmp}/warehouse/receipts/{body['id']}/post",
        {"expected_version": body["version"]},
        key(),
    )
    assert done.status_code == 200, done.content


def _stock(owner: Client, cmp: str) -> dict[str, dict[str, Any]]:  # noqa: F811
    rows = owner.get(f"/api/c/{cmp}/warehouse/stock").json()["stock"]
    return {row["item"]["name"]: row for row in rows}


def test_the_country_of_manufacture_is_a_code(owner: Client, cmp: str) -> None:  # noqa: F811
    made = _item(owner, cmp, name="Pads", manufacturer_other="Brembo", country="vn")
    assert made.status_code == 201, made.content
    assert made.json()["item"]["country"] == "VN"

    # A country the list does not have is typed by hand; a country of the list drops it.
    typed = _item(owner, cmp, name="Hoses", country_other=" Abkhazia ")
    assert typed.json()["item"]["country"] == ""
    assert typed.json()["item"]["country_other"] == "Abkhazia"
    both = _item(owner, cmp, name="Belts", country="TH", country_other="Abkhazia")
    assert both.json()["item"]["country_other"] == ""
    wrong = _item(owner, cmp, name="Discs", country="1!")
    assert wrong.status_code == 400
    assert wrong.json()["error"]["code"] == "COUNTRY_INVALID"


def test_a_row_tells_the_card_and_what_lies_behind_the_quantity(
    owner: Client,
    cmp: str,  # noqa: F811
) -> None:
    context = warehouse(owner, cmp)
    pads = _item(owner, cmp, name="Pads", manufacturer_other="Brembo", country="VN").json()["item"]
    belt = _item(owner, cmp, name="Belt").json()["item"]
    oil = _item(owner, cmp, name="Oil", unit="liter").json()["item"]
    _posted(
        owner,
        cmp,
        context,
        [
            {"item_id": pads["id"], "quantity": 1, "unit_cost": "100"},
            {"item_id": pads["id"], "quantity": 1, "unit_cost": "150"},
            {"item_id": belt["id"], "quantity": 1, "unit_cost": "70"},
            {"item_id": oil["id"], "quantity": "5", "unit_cost": "40"},
        ],
    )

    rows = _stock(owner, cmp)
    assert rows["Pads"]["item"]["manufacturer_name"] == "Brembo"
    assert rows["Pads"]["item"]["country"] == "VN"
    # Two units: no single identifier, the costs from the lowest to the highest.
    assert (rows["Pads"]["units"], rows["Pads"]["unit_id"]) == (2, None)
    assert (float(rows["Pads"]["cost_min"]), float(rows["Pads"]["cost_max"])) == (100, 150)
    assert rows["Pads"]["currency"] == "THB"
    # One unit and one lot are named by their identifiers.
    assert rows["Belt"]["units"] == 1 and rows["Belt"]["unit_id"].startswith("AST-")
    assert rows["Oil"]["units"] == 1 and rows["Oil"]["unit_id"].startswith("LOT-")
    assert float(rows["Oil"]["cost_min"]) == 40
    assert rows["Oil"]["price"] is None and rows["Oil"]["cost_unknown"] is False
