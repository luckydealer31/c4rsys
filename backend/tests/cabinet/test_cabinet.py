"""The personal cabinet (ТЗ § 7.1, § 6.3.6, § 6.6.3, § 18.3.9; decisions Б-1, Б-2, Б-3; part К6)."""

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.test import Client
from django.utils import timezone

from c4rsys.finance.models import ChargeEntry
from c4rsys.fleet.models import VehicleCard, VehicleOwnership
from c4rsys.parties.models import ClientCard
from tests.accounts.helpers import CODE, post
from tests.companies.helpers import key, named_user
from tests.documents.test_registry import _create, _do, _ok
from tests.documents.test_signing import _send
from tests.rentals.scenario import Scenario, issue

pytestmark = pytest.mark.django_db

CABINET = "cabinet.localhost"


def linked_person(owner: Client, s: Scenario) -> Client:
    """Ivan registers and accepts the request of the company to link his client card."""
    ivan = Client()
    named_user(ivan, "ivan@example.com")
    usr = ivan.get("/api/me").json()["user"]["public_id"]
    body = {"card_type": "client", "card_id": s.client, "target": usr}
    link = post(owner, f"/api/c/{s.cmp}/profile-links", body, key()).json()["link"]
    accepted = post(ivan, f"/api/profile-links/{link['id']}/accept", {"fields": []}, key())
    assert accepted.status_code == 200, accepted.content
    return ivan


def test_history_of_a_linked_company(owner: Client, s: Scenario) -> None:
    rented = issue(owner, s, actual_start=(timezone.now() - timedelta(days=1)).isoformat())
    assert rented.status_code == 201, rented.content
    rental_id = rented.json()["rental"]["id"]
    order_id = rented.json()["rental"]["order_id"]
    # The base cost is charged when the rental is calculated; here it is given directly.
    ChargeEntry.objects.create(
        company_id=ClientCard.objects.get(pk=s.client).company_id,
        order_id=order_id,
        client_id=s.client,
        source_type="rental_base",
        source_id=rental_id,
        amount=Decimal("1000.0000"),
        currency_id="THB",
        reason="test",
        created_by_user_id=ClientCard.objects.get(pk=s.client).created_by_user_id,
    )
    ivan = linked_person(owner, s)

    companies = ivan.get("/api/cabinet/companies").json()["companies"]
    assert [c["company"]["display_name"] for c in companies] == ["Test Rent"]
    # § 6.3.6: the history accumulated before the link is there.
    orders = ivan.get(f"/api/cabinet/companies/{companies[0]['link_id']}/history").json()["orders"]
    assert len(orders) == 1
    rental = orders[0]["operations"][0]
    assert rental["type"] == "rental" and rental["vehicle"] == "Toyota Yaris"
    assert rental["issue_place"] == "Phuket"
    base = next(line for line in orders[0]["lines"] if line["source"] == "rental_base")
    assert base["name"] == "Toyota Yaris" and base["amount"] == "1000.00"
    assert "THB" in orders[0]["amounts"]
    # Decision Б-1: nothing internal of the company.
    assert "responsible_employment_id" not in orders[0]

    # § 6.6.3: after a break, the orders from before stay and new ones are not shared.
    link_id = owner.get(f"/api/c/{s.cmp}/profile-links?card_type=client&card_id={s.client}").json()[
        "links"
    ][0]["id"]
    post(owner, f"/api/c/{s.cmp}/profile-links/{link_id}/break", {"initiator": "client"}, key())
    after = ivan.get(f"/api/cabinet/companies/{companies[0]['link_id']}/history").json()
    assert len(after["orders"]) == 1
    assert ivan.get("/api/cabinet/companies").json()["companies"][0]["state"] == "broken"


def test_documents_shown_and_signed_in_the_cabinet(owner: Client, s: Scenario) -> None:
    ivan = linked_person(owner, s)
    linked = _ok(_create(owner, s, links=[{"object_type": "client", "object_id": s.client}]), 201)
    assert ivan.get("/api/cabinet/documents").json()["documents"] == []

    # Decision Б-2: a linked document is shown only with «Show to the client».
    shown = _ok(_do(owner, s, linked, "client-visibility", visible=True))
    assert shown["show_to_client"] is True
    found = ivan.get("/api/cabinet/documents").json()["documents"]
    assert [d["id"] for d in found] == [linked["id"]] and found[0]["signer"] is None

    signed_doc = _ok(_create(owner, s, title="Contract"), 201)
    assert _send(owner, s, signed_doc, ("client", s.client)).status_code == 200
    mine = {d["title"]: d for d in ivan.get("/api/cabinet/documents").json()["documents"]}
    assert mine["Contract"]["signer"]["state"] == "pending"
    detail = ivan.get(f"/api/cabinet/documents/{signed_doc['id']}").json()["document"]
    assert detail["has_file"]
    # The file of a shown document is read by the client.
    assert ivan.get(f"/api/files/{detail['file_id']}").status_code == 200

    url = f"/api/cabinet/documents/{signed_doc['id']}"
    assert post(ivan, f"{url}/code", {}, key()).json()["sent_to"] == "i***@example.com"
    wrong = post(ivan, f"{url}/decide", {"code": "000000", "decision": "sign"}, key())
    assert wrong.status_code == 422
    done = post(ivan, f"{url}/decide", {"code": CODE, "decision": "sign"}, key())
    assert done.status_code == 200, done.content
    assert done.json()["document"]["signer"]["state"] == "signed"
    status = owner.get(f"/api/c/{s.cmp}/documents/{signed_doc['id']}").json()["document"]
    assert status["signing"]["state"] == "signed"

    # A stranger does not see it.
    other = Client()
    named_user(other, "other@example.com")
    assert other.get(url).status_code == 404


def test_garage_and_the_cabinet_domain(owner: Client, s: Scenario) -> None:
    ivan = linked_person(owner, s)
    card = VehicleCard.objects.get(pk=s.vehicle)
    from c4rsys.accounts.models import UserAccount

    user = UserAccount.objects.get(public_id=ivan.get("/api/me").json()["user"]["public_id"])
    VehicleOwnership.objects.create(
        vehicle=card.vehicle, owner_user_id=user.id, started_at=timezone.now()
    )
    garage = ivan.get("/api/cabinet/garage").json()["vehicles"]
    assert [v["vehicle_id"] for v in garage] == [card.vehicle.public_id]

    # Decision of 30.10.2026: the cabinet domain serves only the personal interface.
    assert ivan.get("/api/site", HTTP_HOST=CABINET).json() == {"cabinet": True}
    assert ivan.get("/api/site").json() == {"cabinet": False}
    blocked: Any = owner.get(f"/api/c/{s.cmp}/orders", HTTP_HOST=CABINET)
    assert blocked.status_code == 404 and blocked.json()["error"]["code"] == "CABINET_HOST"
    assert owner.get("/api/cabinet/companies", HTTP_HOST=CABINET).status_code == 200
