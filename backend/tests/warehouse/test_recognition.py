"""Reading a document of a supplier into a receipt (customer's decision of 20.11.2026). The outer
service is not chosen yet: the tests put a function in its socket."""

from typing import Any

import pytest
from django.test import Client

from c4rsys.warehouse import recognition
from tests.accounts.helpers import post
from tests.companies.helpers import key
from tests.files.helpers import PNG, upload
from tests.warehouse.test_receipts import cmp, warehouse  # noqa: F401

pytestmark = pytest.mark.django_db

ANSWER: dict[str, Any] = {
    "supplier_name": "  Phuket  Parts Co ",
    "document_date": "2026-11-12",
    "document_number": "INV-77",
    "currency": "thb",
    "lines": [
        {"name": "Brake pads front", "article": "bp-100", "quantity": "2", "unit_cost": "1 250,50"},
        {"name": "Oil filter", "article": None, "quantity": "4", "unit_cost": None},
        {"name": "Wiper blade 600", "article": "WB-600", "quantity": "minus", "unit_cost": "-5"},
        {"name": "   ", "article": "X", "quantity": "1", "unit_cost": "1"},
    ],
}


def _document(owner: Client) -> str:
    made = upload(owner, "warehouse_document", key=next(iter(key().values())))
    assert made.status_code == 201, made.content
    return str(made.json()["file"]["id"])


def _read(owner: Client, cmp: str, document: str | None) -> Any:  # noqa: F811
    return post(
        owner, f"/api/c/{cmp}/warehouse/receipts/recognize", {"document_id": document}, key()
    )


@pytest.fixture
def service(settings: Any) -> Any:
    """A service in the socket: it answers with what the test has put into `answer`."""
    box: dict[str, Any] = {"answer": ANSWER, "seen": []}

    def fake(content: bytes, content_type: str) -> Any:
        box["seen"].append((len(content), content_type))
        if isinstance(box["answer"], Exception):
            raise box["answer"]
        return box["answer"]

    recognition.register("test", fake)
    settings.C4R_INVOICE_RECOGNITION = "test"
    yield box
    recognition._recognizers.pop("test", None)


def test_without_a_service_the_feature_is_off(owner: Client, cmp: str) -> None:  # noqa: F811
    assert owner.get("/api/catalog/warehouse-options").json()["invoice_recognition"] is False
    refused = _read(owner, cmp, _document(owner))
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "RECOGNITION_NOT_CONFIGURED"


def test_the_lines_are_compared_with_the_catalog(owner: Client, cmp: str, service: Any) -> None:  # noqa: F811
    assert owner.get("/api/catalog/warehouse-options").json()["invoice_recognition"] is True
    items = f"/api/c/{cmp}/warehouse/items"
    pads = post(
        owner, items, {"name": "Колодки передние", "unit": "piece", "article": "BP-100"}, key()
    )
    oil = post(owner, items, {"name": "oil filter", "unit": "piece"}, key()).json()["item"]
    wiper = post(owner, items, {"name": "Wiper blade 600 mm", "unit": "piece"}, key()).json()[
        "item"
    ]

    read = _read(owner, cmp, _document(owner))
    assert read.status_code == 200, read.content
    body = read.json()
    assert service["seen"] == [(len(PNG), "image/png")]
    assert (body["supplier_name"], body["document_date"]) == ("Phuket Parts Co", "2026-11-12")
    assert (body["document_number"], body["currency"]) == ("INV-77", "THB")
    by_article, by_name, unknown = body["lines"]  # the line without a name is dropped
    # The article finds the item whatever its name here is; the number is read with its comma.
    assert by_article["item"]["id"] == pads.json()["item"]["id"]
    assert (by_article["quantity"], by_article["unit_cost"]) == ("2", "1250.50")
    # Without an article the exact name does, whatever the capitals.
    assert by_name["item"]["id"] == oil["id"]
    assert by_name["unit_cost"] is None
    # Nothing found: the likely items are named, the wrong numbers are as good as not read.
    assert unknown["item"] is None
    assert [c["id"] for c in unknown["candidates"]] == [wiper["id"]]
    assert (unknown["quantity"], unknown["unit_cost"]) == (None, None)
    # Nothing is written by reading.
    assert owner.get(f"/api/c/{cmp}/warehouse/receipts").json()["receipts"] == []


def test_a_failure_of_the_service_and_a_wrong_file(owner: Client, cmp: str, service: Any) -> None:  # noqa: F811
    document = _document(owner)
    for broken in (RuntimeError("down"), "not a document", {"lines": "none"}):
        service["answer"] = broken
        failed = _read(owner, cmp, document)
        assert failed.status_code == 422
        assert failed.json()["error"]["code"] == "RECOGNITION_FAILED"
    for wrong in (None, "0190a000-0000-7000-8000-000000000000"):
        refused = _read(owner, cmp, wrong)
        assert refused.status_code == 400
        assert refused.json()["error"]["code"] == "FILE_NOT_USABLE"
