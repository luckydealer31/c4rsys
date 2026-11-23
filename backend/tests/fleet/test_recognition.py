"""Reading a paper of a vehicle into the card of a new one (customer's decision of 22.11.2026).
The outer service is replaced: the tests put a function in its socket."""

from typing import Any

import pytest
from django.test import Client

from c4rsys.fleet import recognition
from c4rsys.fleet.models import VehicleMake, VehicleModel
from tests.accounts.helpers import post
from tests.companies.helpers import create_company, key
from tests.files.helpers import upload

pytestmark = pytest.mark.django_db

ANSWER: dict[str, Any] = {
    "vehicle_type": "passenger_car",
    "make": " Toyota ",
    "model": "Yaris  Ativ",
    "year": 2022,
    "plate": "1กข 1234",
    "plate_country": "th",
    "vin": "mr2 k29f3 0n1234567",
    "body_number": None,
    "chassis_number": "MR2K29F30N1234567",
    "frame_number": None,
    "engine_number": "3NR-1234567",
    "color": "White",
    "body_type": "sedan",
    "fuel_type": "kerosene",
    "transmission": "CVT",
    "drive": None,
    "engine_volume_cc": "1197",
    "power_hp": -5,
    "seats": 5,
    "doors": True,
    "curb_weight_kg": None,
    "gross_weight_kg": 1500,
    "owner_name": "  Somchai   Jaidee ",
    "owner_kind": "Person",
}


@pytest.fixture
def cmp(owner: Client) -> str:
    return str(create_company(owner).json()["company"]["public_id"])


def _paper(owner: Client) -> str:
    made = upload(owner, "vehicle_paper", key=next(iter(key().values())))
    assert made.status_code == 201, made.content
    return str(made.json()["file"]["id"])


def _read(owner: Client, cmp: str, document: str | None) -> Any:
    return post(owner, f"/api/c/{cmp}/vehicles/recognize", {"document_id": document}, key())


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


def test_without_a_service_the_feature_is_off(owner: Client, cmp: str) -> None:
    assert owner.get("/api/catalog/vehicle-options").json()["paper_recognition"] is False
    refused = _read(owner, cmp, _paper(owner))
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "RECOGNITION_NOT_CONFIGURED"


def test_a_paper_is_read_and_checked(owner: Client, cmp: str, service: Any) -> None:
    assert owner.get("/api/catalog/vehicle-options").json()["paper_recognition"] is True
    # A make and a model of the catalog loaded with the system.
    model = VehicleModel.objects.select_related("make").order_by("-name").first()
    assert model is not None
    make: VehicleMake = model.make
    service["answer"] = {**ANSWER, "make": f" {make.name} ", "model": model.name}

    read = _read(owner, cmp, _paper(owner))
    assert read.status_code == 200, read.content
    body = read.json()
    assert service["seen"] == [(72, "image/png")]
    # What was read, tidied.
    assert body["make"] == make.name
    assert body["model"] == model.name
    assert body["year"] == 2022
    assert body["plate"] == "1กข 1234"
    assert body["plate_country"] == "TH"
    assert body["vin"] == "MR2K29F30N1234567"
    assert body["engine_number"] == "3NR-1234567"
    assert body["transmission"] == "cvt"
    assert body["engine_volume_cc"] == 1197
    assert body["gross_weight_kg"] == 1500
    assert body["owner_name"] == "Somchai Jaidee"
    assert body["owner_kind"] == "person"
    # A value that is not allowed is as good as not read.
    assert body["fuel_type"] == ""
    assert body["power_hp"] is None
    assert body["doors"] is None
    assert body["body_number"] == ""
    # The make and the model are found in the catalog.
    assert body["make_code"] == make.code
    assert body["model_code"] == model.code
    assert body["catalog_type"] == make.vehicle_type
    # Nothing is made by the reading.
    assert owner.get(f"/api/c/{cmp}/vehicles").json()["vehicles"] == []


def test_a_failure_of_the_service_and_a_wrong_file(owner: Client, cmp: str, service: Any) -> None:
    service["answer"] = RuntimeError("the service is down")
    failed = _read(owner, cmp, _paper(owner))
    assert failed.status_code == 422
    assert failed.json()["error"]["code"] == "RECOGNITION_FAILED"
    service["answer"] = ["not", "an", "object"]
    assert _read(owner, cmp, _paper(owner)).json()["error"]["code"] == "RECOGNITION_FAILED"
    # A file of another purpose is not read.
    other = upload(owner, "warehouse_document", key=next(iter(key().values()))).json()["file"]["id"]
    refused = _read(owner, cmp, other)
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "FILE_NOT_USABLE"


def test_schemas_stay_within_the_limit_of_the_outer_service() -> None:
    """The service refuses a schema with more than 16 fields of two types (it did so with the
    paper of a vehicle on 23.11.2026), so every schema sent to it is counted."""
    from c4rsys.finance import expense_drafts
    from c4rsys.warehouse import recognition as invoices
    from c4rsys.warehouse.recognition_anthropic import _schema

    def unions(node: object) -> int:
        if isinstance(node, list):
            return sum(unions(v) for v in node)
        if not isinstance(node, dict):
            return 0
        return ("anyOf" in node) + sum(unions(v) for v in node.values())

    for schema in (recognition.SCHEMA, invoices.SCHEMA, expense_drafts.SCHEMA):
        assert unions(_schema(schema)) <= 16


def test_what_a_registration_paper_names_besides_is_read() -> None:
    """Customer's decision of 23.11.2026: the power in kilowatts, the category, the ecological
    class, the approval of the type and the special marks are read as written."""
    paper = recognition.parse(
        {
            "power_kw": "81,0",
            "vehicle_category": " B / M1 ",
            "eco_class": "пятый",
            "type_approval": "ТС RU E-RU.MT02.00003.И5",
            "special_marks": "Договор лизинга",
        }
    )
    assert paper.texts["power_kw"] == "81.0"
    assert paper.texts["vehicle_category"] == "B / M1"
    assert paper.texts["eco_class"] == "пятый"
    assert paper.texts["type_approval"].startswith("ТС RU")
    assert paper.texts["special_marks"] == "Договор лизинга"
    assert recognition.parse({"power_kw": "about 80"}).texts["power_kw"] == ""
