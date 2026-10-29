"""The setup wizard, part К1 (ТЗ § 22.1; block 6 plan of 29.10.2026)."""

from typing import Any

import pytest
from django.test import Client

from tests.accounts.helpers import post
from tests.companies.helpers import create_company, key
from tests.rentals.scenario import add_location, build

pytestmark = pytest.mark.django_db


def _readiness(client: Client, cmp: str) -> dict[str, Any]:
    body: dict[str, Any] = client.get(f"/api/c/{cmp}/readiness").json()
    return body


def test_sections_and_explicit_answers(owner: Client) -> None:
    s = build(owner)
    sections = _readiness(owner, s.cmp)["sections"]
    assert sections["profile"] and sections["finance"] and sections["location"]
    assert sections["catalog"] and sections["vehicles"] and sections["check"]
    # Employees and documents are done by an explicit answer (§ 22.1.10–22.1.11).
    assert not sections["employees"] and not sections["documents"]
    for section, value in (("employees", "alone"), ("documents", "none")):
        answered = post(
            owner, f"/api/c/{s.cmp}/setup/choice", {"section": section, "value": value}, key()
        )
        assert answered.status_code == 200, answered.content
    body = _readiness(owner, s.cmp)
    assert body["sections"]["employees"] and body["sections"]["documents"]
    assert body["choices"] == {"employees": "alone", "documents": "none"}
    wrong = post(
        owner, f"/api/c/{s.cmp}/setup/choice", {"section": "tariffs", "value": "none"}, key()
    )
    assert wrong.status_code == 400


def test_service_and_standalone_services(owner: Client) -> None:
    s = build(owner)
    post(owner, f"/api/c/{s.cmp}/locations/{s.location}/contexts", {"type": "service"}, key())
    assert not _readiness(owner, s.cmp)["sections"]["catalog"]
    work = post(
        owner,
        f"/api/c/{s.cmp}/service/works",
        {"name": "Oil change", "pricing": "fixed", "price": "500"},
        key(),
    )
    assert work.status_code == 201, work.content
    body = _readiness(owner, s.cmp)
    service = next(c for c in body["locations"][0]["contexts"] if c["type"] == "service")
    assert service["ready"] and body["sections"]["catalog"]


def test_standalone_service_makes_rental_ready_without_vehicles(owner: Client) -> None:
    cmp = create_company(
        owner, display_name="Seats", timezone="Asia/Bangkok", main_currency="THB"
    ).json()["company"]["public_id"]
    add_location(owner, cmp, "Patong")
    rental = _readiness(owner, cmp)["locations"][0]["contexts"][0]
    assert not rental["ready"]
    service = post(
        owner,
        f"/api/c/{cmp}/extra-services",
        {"name": "Child seat", "pricing": "time", "unit": "day", "price": "100"},
        key(),
    )
    assert service.status_code == 201, service.content
    body = _readiness(owner, cmp)
    # § 22.1.8: a standalone additional service is a way of rental work; no vehicle is needed.
    assert body["locations"][0]["contexts"][0]["ready"] and body["first_operation_ready"]
