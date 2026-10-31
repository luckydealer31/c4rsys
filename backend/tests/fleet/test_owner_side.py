"""What the owner receives: the history and finance of a managed vehicle, the global documents
with versions and the company copies, the sale into the personal garage and the request about a
wrong transfer, the change of the owner (ТЗ § 7.1.2, § 8.2.3–8.2.7, § 8.3.4–8.3.10, § 8.3.15,
§ 9.1.4, § 11.9.5)."""

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from django.test import Client
from django.utils import timezone

from c4rsys.fleet import services as fleet
from c4rsys.fleet.models import (
    VehicleCard,
    VehicleManagementConfirmation,
    VehicleOwnerRequest,
    VehicleOwnership,
)
from c4rsys.parties.models import ClientCard
from c4rsys.platform.commands import Actor, CommandContext
from tests.accounts.helpers import post
from tests.companies.helpers import key
from tests.documents.test_registry import _create
from tests.files.helpers import upload
from tests.fleet.test_owner_requests import answer, ask, person
from tests.rentals.scenario import Scenario, build
from tests.warehouse.test_operations import location as warehouse_location
from tests.warehouse.test_operations import warehouse_of
from tests.warehouse.test_sales import lines_url, put

pytestmark = pytest.mark.django_db


@pytest.fixture
def s(owner: Client) -> Scenario:
    return build(owner)


def _user_id(email: str) -> str:
    from c4rsys.accounts import services as accounts

    user = accounts.find_user_by_email(email)
    assert user is not None
    return str(user.id)


def _ctx() -> CommandContext:
    return CommandContext(actor=Actor(user_id=uuid4()), command="test", idempotency_key="k")


def _managed(owner: Client, s: Scenario, usr: str) -> dict[str, Any]:
    created = post(
        owner,
        f"/api/c/{s.cmp}/vehicles",
        {
            "name": "Civic",
            "relation": "managed",
            "location_id": s.location,
            "odometer_unit": "km",
            "vin": "JHMFA16586S000001",
            "owner_kind": "profile",
            "owner_lookup": usr,
        },
        key(),
    )
    assert created.status_code == 201, created.content
    card: dict[str, Any] = created.json()["vehicle"]
    return card


def _categories(owner: Client, s: Scenario, card: str, categories: list[str]) -> Any:
    return owner.put(
        f"/api/c/{s.cmp}/vehicles/{card}/disclosure",
        data={"categories": categories},
        content_type="application/json",
        headers=key(),
    )


def _reading(card_id: str, value: str, at: Any) -> None:
    card = VehicleCard.objects.get(pk=card_id)
    fleet.record_reading(_ctx(), card, measured_at=at, value=Decimal(value), source_type="test")


def test_history_is_passed_only_after_three_yes_and_by_category(owner: Client, s: Scenario) -> None:
    somchai, usr = person("somchai@example.com")
    card = _managed(owner, s, usr)
    assert _categories(owner, s, card["id"], ["mileage_fuel", "nonsense"]).status_code == 400
    set_ = _categories(owner, s, card["id"], ["mileage_fuel", "service", "finance_result"])
    assert set_.status_code == 200, set_.content
    _reading(card["id"], "1000", timezone.now() - timedelta(days=3))
    request = ask(owner, s.cmp, card["id"]).json()["request"]
    # § 8.3.4: «it is mine» alone passes nothing; the offered categories are listed (§ 8.3.15).
    answer(somchai, request["id"], ownership=True)
    vhl = card["vehicle_id"]
    garage = somchai.get(f"/api/cabinet/garage/{vhl}").json()["vehicle"]
    manager = garage["managers"][0]
    assert manager["confirmed"] is False
    assert [(c["category"], c["passed"]) for c in manager["categories"]] == [
        ("mileage_fuel", False),
        ("service", False),
        ("finance_result", False),
    ]
    assert all("records" not in c for c in manager["categories"])
    # Three «yes»: the history before the confirmation is passed too (§ 8.3.6).
    answer(somchai, request["id"], link=True, management=True)
    manager = somchai.get(f"/api/cabinet/garage/{vhl}").json()["vehicle"]["managers"][0]
    assert manager["confirmed"] is True
    mileage = next(c for c in manager["categories"] if c["category"] == "mileage_fuel")
    assert mileage["passed"] and mileage["records"][0]["odometer"] == "1000.0"
    result = next(c for c in manager["categories"] if c["category"] == "finance_result")
    assert result["totals"] == []
    # A reduction stops new records; the received ones stay (§ 8.3.15).
    assert _categories(owner, s, card["id"], ["service"]).status_code == 200
    _reading(card["id"], "1200", timezone.now() + timedelta(seconds=1))
    manager = somchai.get(f"/api/cabinet/garage/{vhl}").json()["vehicle"]["managers"][0]
    mileage = next(c for c in manager["categories"] if c["category"] == "mileage_fuel")
    assert [r["odometer"] for r in mileage["records"]] == ["1000.0"]
    assert mileage["until"] is not None
    # The end of the management stops the updates, the received history stays (§ 8.3.17).
    current = owner.get(f"/api/c/{s.cmp}/vehicles/{card['id']}").json()["vehicle"]
    ended = post(
        owner,
        f"/api/c/{s.cmp}/vehicles/{card['id']}/end-management",
        {"expected_version": current["version"], "reason": "Contract over"},
        key(),
    )
    assert ended.status_code == 200, ended.content
    confirmation = VehicleManagementConfirmation.objects.get()
    assert confirmation.end_reason == "management_ended"
    manager = somchai.get(f"/api/cabinet/garage/{vhl}").json()["vehicle"]["managers"][0]
    assert manager["confirmed"] is False
    assert [c["category"] for c in manager["categories"] if c["passed"]] == [
        "mileage_fuel",
        "service",
        "finance_result",
    ]


def test_global_documents_and_company_copies(owner: Client, s: Scenario) -> None:
    somchai, usr = person("somchai@example.com")
    card = _managed(owner, s, usr)
    copy = _create(
        owner,
        s,
        title="Policy copy",
        links=[{"object_type": "vehicle", "object_id": card["id"]}],
    )
    assert copy.status_code == 201, copy.content
    url = f"/api/c/{s.cmp}/vehicles/{card['id']}"
    docs = owner.get(url).json()["vehicle"]["global_documents"]
    # § 8.2.7: before the owner is confirmed the copies stay in the card, marked so.
    assert docs["access"] is None and docs["documents"] == []
    assert [(c["title"], c["owner_decision"]) for c in docs["copies"]] == [("Policy copy", None)]
    request = ask(owner, s.cmp, card["id"]).json()["request"]
    answer(somchai, request["id"], ownership=True)
    vhl = card["vehicle_id"]
    # The owner manages the documents: a new document with a file, then a new version.
    uploaded = upload(somchai, "vehicle_document", b"%PDF-1.4\n" + b"0" * 50, key="vd-1")
    assert uploaded.status_code == 201, uploaded.content
    added = post(
        somchai,
        f"/api/cabinet/garage/{vhl}/documents",
        {
            "doc_type": "insurance",
            "title": "Policy",
            "details": "No. 1",
            "valid_until": "2027-10-31",
            "file_id": uploaded.json()["file"]["id"],
        },
        key(),
    )
    assert added.status_code == 201, added.content
    doc = added.json()["vehicle"]["documents"][0]
    again = post(
        somchai,
        f"/api/cabinet/garage/{vhl}/documents",
        {"document_id": doc["id"], "details": "No. 2", "valid_until": "2028-10-31"},
        key(),
    )
    versions = again.json()["vehicle"]["documents"][0]["versions"]
    assert [(v["number"], v["details"]) for v in versions] == [(2, "No. 2"), (1, "No. 1")]
    # The company reads nothing while only the ownership is confirmed.
    assert owner.get(url).json()["vehicle"]["global_documents"]["access"] is None
    # The copies of a company whose management is not confirmed are not offered.
    assert again.json()["vehicle"]["copies"] == []
    answer(somchai, request["id"], link=True, management=True)
    docs = owner.get(url).json()["vehicle"]["global_documents"]
    assert docs["access"] == "management" and len(docs["documents"]) == 1
    version_file = owner.get(f"{url}/global-documents/versions/{versions[1]['id']}/file")
    assert version_file.status_code == 200
    offered = somchai.get(f"/api/cabinet/garage/{vhl}").json()["vehicle"]["copies"]
    assert offered[0]["company"] == "Test Rent"
    decide_url = (
        f"/api/cabinet/garage/{vhl}/copies/{offered[0]['card_id']}/{offered[0]['version_id']}"
        "/decide"
    )
    decided = post(somchai, decide_url, {"decision": "accepted", "document_id": doc["id"]}, key())
    assert decided.status_code == 200, decided.content
    versions = decided.json()["vehicle"]["documents"][0]["versions"]
    assert (versions[0]["number"], versions[0]["source"]) == (3, "company_copy")
    copies = owner.get(url).json()["vehicle"]["global_documents"]["copies"]
    assert copies[0]["owner_decision"] == "accepted"
    twice = post(somchai, decide_url, {"decision": "kept_private"}, key())
    assert twice.json()["error"]["code"] == "COPY_ALREADY_DECIDED"


def _sell(owner: Client, s: Scenario, buyer_user: Any) -> tuple[str, str, str]:
    place = warehouse_location(owner, s.cmp, "Store")
    ctx = warehouse_of(place)
    car = post(
        owner,
        f"/api/c/{s.cmp}/vehicles",
        {"name": "Old Vios", "location_id": place["id"], "odometer_unit": "km"},
        key(),
    ).json()["vehicle"]
    off = owner.patch(
        f"/api/c/{s.cmp}/vehicles/{car['id']}",
        data={"contexts": [], "expected_version": car["version"]},
        content_type="application/json",
        headers=key(),
    ).json()["vehicle"]
    post(
        owner,
        f"/api/c/{s.cmp}/vehicles/{car['id']}/to-warehouse",
        {"context_id": ctx, "expected_version": off["version"]},
        key(),
    )
    ClientCard.objects.filter(pk=s.client).update(linked_user_id=buyer_user)
    order = post(
        owner, f"/api/c/{s.cmp}/warehouse/sales/start", {"client_id": s.client}, key()
    ).json()["order"]["id"]
    put(owner, f"/api/c/{s.cmp}/warehouse/vehicles/{car['id']}/price", {"amount": "350000"})
    line = post(
        owner, lines_url(s.cmp, order), {"context_id": ctx, "vehicle_id": car["id"]}, key()
    ).json()["lines"][0]
    version = owner.get(f"/api/c/{s.cmp}/orders/{order}").json()["order"]["version"]
    closed = post(
        owner, f"/api/c/{s.cmp}/orders/{order}/close", {"expected_version": version}, key()
    )
    assert closed.status_code == 200, closed.content
    return car["id"], line["id"], order


def test_sale_puts_the_vehicle_into_the_buyers_garage_and_the_reversal(
    owner: Client, s: Scenario
) -> None:
    buyer, _usr = person("buyer@example.com")
    buyer_id = _user_id("buyer@example.com")
    card_id, line_id, order = _sell(owner, s, buyer_id)
    garage = buyer.get("/api/cabinet/garage").json()["vehicles"]
    assert [v["vehicle_id"] for v in garage] == [
        VehicleCard.objects.get(pk=card_id).vehicle.public_id
    ]
    ownership = VehicleOwnership.objects.get(owner_user_id=buyer_id)
    assert (ownership.basis, str(ownership.order_id)) == ("sale", order)
    # The seller cannot undo it alone (§ 8.3.9, § 11.9.5).
    version = owner.get(f"/api/c/{s.cmp}/orders/{order}").json()["order"]["version"]
    post(
        owner,
        f"/api/c/{s.cmp}/orders/{order}/reopen",
        {"expected_version": version, "reason": "Wrong buyer"},
        key(),
    )
    cancel_url = f"{lines_url(s.cmp, order)}/{line_id}/cancel"
    refused = post(owner, cancel_url, {"reason": "Wrong buyer"}, key())
    assert refused.json()["error"]["code"] == "VEHICLE_OWNERSHIP_TRANSFERRED"
    asked = post(
        owner,
        f"/api/c/{s.cmp}/vehicles/{card_id}/transfer-reversal",
        {"reason": "Sold to the wrong person"},
        key(),
    )
    assert asked.status_code == 201, asked.content
    assert asked.json()["vehicle"]["transfer_reversals"][0]["state"] == "open"
    pending = buyer.get("/api/cabinet/reversals").json()["reversals"]
    assert pending[0]["reason"] == "Sold to the wrong person"
    accepted = post(
        buyer, f"/api/cabinet/reversals/{pending[0]['id']}/answer", {"decision": "accept"}, key()
    )
    assert accepted.status_code == 200, accepted.content
    assert buyer.get("/api/cabinet/garage").json()["vehicles"] == []
    # Now the wrong sale is cancelled: the vehicle is back in the seller's warehouse.
    cancelled = post(owner, cancel_url, {"reason": "Wrong buyer"}, key())
    assert cancelled.status_code == 200, cancelled.content
    card = VehicleCard.objects.get(pk=card_id)
    assert card.state == "active" and card.storage_context_id is not None


def test_works_before_the_sale_are_passed_to_the_buyer(owner: Client, s: Scenario) -> None:
    buyer, _usr = person("buyer@example.com")
    buyer_id = _user_id("buyer@example.com")
    card_id, _line, _order = _sell(owner, s, buyer_id)
    vhl = VehicleCard.objects.get(pk=card_id).vehicle.public_id
    detail = buyer.get(f"/api/cabinet/garage/{vhl}").json()["vehicle"]
    history = detail["seller_history"]
    assert history["company"] == "Test Rent"
    assert history["works"] == [] and history["rules"] == []


def test_change_of_owner_ends_the_confirmed_management(owner: Client, s: Scenario) -> None:
    """§ 8.3.10: company B manages the vehicle of company A; A sells it to a person."""
    from c4rsys.companies.models import Company
    from tests.companies.helpers import create_company

    buyer, _usr = person("buyer@example.com")
    buyer_id = _user_id("buyer@example.com")
    created = create_company(owner, key="c-b", display_name="Manager B").json()["company"]
    other = Company.objects.get(public_id=created["public_id"])
    _sold_with_manager(owner, s, buyer_id, other)
    managed = VehicleCard.objects.get(company=other)
    confirmation = VehicleManagementConfirmation.objects.get(vehicle_card=managed)
    assert confirmation.end_reason == "ownership_changed"
    request = VehicleOwnerRequest.objects.get(vehicle_card=managed, state="open")
    assert str(request.target_user_id) == buyer_id
    claim = managed.owner_claims.order_by("-created_at").first()
    assert claim is not None and claim.source == "ownership_change"


def _sold_with_manager(owner: Client, s: Scenario, buyer_id: str, other: Any) -> None:
    from c4rsys.companies.models import Location
    from c4rsys.fleet.models import VehicleOwnerClaim

    place = warehouse_location(owner, s.cmp, "Store")
    ctx = warehouse_of(place)
    car = post(
        owner,
        f"/api/c/{s.cmp}/vehicles",
        {"name": "Old Vios", "location_id": place["id"], "odometer_unit": "km"},
        key(),
    ).json()["vehicle"]
    seller = VehicleCard.objects.get(pk=car["id"])
    VehicleOwnership.objects.create(
        vehicle_id=seller.vehicle_id,
        owner_company_id=seller.company_id,
        started_at=timezone.now() - timedelta(days=10),
    )
    location = Location.objects.create(company=other, name="B")
    managed = VehicleCard.objects.create(
        company=other,
        vehicle_id=seller.vehicle_id,
        name="Managed by B",
        relation="managed",
        accounting_location=location,
        odometer_unit="km",
        created_by_user_id=uuid4(),
    )
    claim = VehicleOwnerClaim.objects.create(
        company=other,
        vehicle_card=managed,
        kind="profile",
        lookup=seller.company.public_id,
        created_by_user_id=uuid4(),
    )
    request = VehicleOwnerRequest.objects.create(
        company=other,
        vehicle_card=managed,
        vehicle_id=seller.vehicle_id,
        claim=claim,
        target_label=seller.company.public_id,
        target_company_id=seller.company_id,
        state="complete",
        created_by_user_id=uuid4(),
        expires_at=timezone.now() + timedelta(days=1),
    )
    VehicleManagementConfirmation.objects.create(
        company=other,
        vehicle_card=managed,
        vehicle_id=seller.vehicle_id,
        request=request,
        owner_company_id=seller.company_id,
        started_at=timezone.now() - timedelta(days=5),
    )
    off = owner.patch(
        f"/api/c/{s.cmp}/vehicles/{car['id']}",
        data={"contexts": [], "expected_version": car["version"]},
        content_type="application/json",
        headers=key(),
    ).json()["vehicle"]
    post(
        owner,
        f"/api/c/{s.cmp}/vehicles/{car['id']}/to-warehouse",
        {"context_id": ctx, "expected_version": off["version"]},
        key(),
    )
    ClientCard.objects.filter(pk=s.client).update(linked_user_id=buyer_id)
    order = post(
        owner, f"/api/c/{s.cmp}/warehouse/sales/start", {"client_id": s.client}, key()
    ).json()["order"]["id"]
    put(owner, f"/api/c/{s.cmp}/warehouse/vehicles/{car['id']}/price", {"amount": "350000"})
    line = post(
        owner, lines_url(s.cmp, order), {"context_id": ctx, "vehicle_id": car["id"]}, key()
    ).json()["lines"][0]
    version = owner.get(f"/api/c/{s.cmp}/orders/{order}").json()["order"]["version"]
    closed = post(
        owner, f"/api/c/{s.cmp}/orders/{order}/close", {"expected_version": version}, key()
    )
    assert closed.status_code == 200, closed.content
    del line
    seller_period = VehicleOwnership.objects.get(owner_company_id=seller.company_id)
    assert seller_period.ended_at is not None
