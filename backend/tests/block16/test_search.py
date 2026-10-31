"""The single search line and the search in the sections (ТЗ § 2.8.1–2.8.3)."""

from datetime import timedelta
from typing import Any

import pytest
from django.test import Client
from django.utils import timezone

from c4rsys.platform.search import parse
from tests.accounts.helpers import post
from tests.companies.helpers import create_company, hire, key, named_user
from tests.rentals.scenario import add_client, build
from tests.rentals.scenario import issue as scenario_issue

pytestmark = pytest.mark.django_db


def issue(owner: Client, s: Any) -> Any:
    """An issue an hour ago: the run date does not matter."""
    return scenario_issue(owner, s, actual_start=(timezone.now() - timedelta(hours=1)).isoformat())


def _search(client: Client, cmp: str, q: str, kinds: str = "") -> dict[str, list[dict[str, Any]]]:
    params = {"q": q, **({"kinds": kinds} if kinds else {})}
    response = client.get(f"/api/c/{cmp}/search", params)
    assert response.status_code == 200, response.content
    return {g["kind"]: g["hits"] for g in response.json()["groups"]}


def test_query_parsing() -> None:
    assert parse("№ 12").number == 12
    assert parse("#7").number == 7
    assert parse("0042").number == 42
    assert parse("Yaris").number is None
    assert parse("  a   b ").text == "a b"
    assert parse("019a0000-0000-7000-8000-000000000000").uuid is not None


def test_finds_records_of_each_section(owner: Client) -> None:
    s = build(owner)
    rental = issue(owner, s).json()["rental"]
    order_number = owner.get(f"/api/c/{s.cmp}/orders/{rental['order_id']}").json()["order"][
        "number"
    ]
    task = post(owner, f"/api/c/{s.cmp}/tasks", {"title": "Wash the Yaris"}, key()).json()["task"]

    # § 2.8.3: an order by its number — an exact match.
    found = _search(owner, s.cmp, f"№ {order_number}")
    assert [h["id"] for h in found["order"]] == [rental["order_id"]]
    assert found["order"][0]["exact"] is True
    # A vehicle by a part of the name; a task by a part of the title; a client by the name.
    found = _search(owner, s.cmp, "yar")
    assert [h["id"] for h in found["vehicle"]] == [s.vehicle]
    assert found["vehicle"][0]["exact"] is False
    assert [h["id"] for h in found["task"]] == [task["id"]]
    assert [h["id"] for h in _search(owner, s.cmp, "Petrov")["client"]] == [s.client]
    # The c4rsys vehicle ID is an exact match.
    vehicle_id = owner.get(f"/api/c/{s.cmp}/vehicles/{s.vehicle}").json()["vehicle"]["vehicle_id"]
    assert _search(owner, s.cmp, vehicle_id.lower())["vehicle"][0]["exact"] is True
    # Exact matches first.
    add_client(owner, s.cmp, first="Petrova", phone="+66812345679")
    post(owner, f"/api/c/{s.cmp}/tasks", {"title": "Wash"}, key())
    hits = _search(owner, s.cmp, "wash")["task"]
    assert hits[0]["exact"] is True and hits[0]["label"] != task["number"]
    # Only the asked groups.
    assert set(_search(owner, s.cmp, "yar", kinds="vehicle")) == {"vehicle"}
    assert owner.get(f"/api/c/{s.cmp}/search", {"q": "x", "kinds": "nope"}).status_code == 400


def test_section_lists_search(owner: Client) -> None:
    s = build(owner)
    rental = issue(owner, s).json()["rental"]
    order = owner.get(f"/api/c/{s.cmp}/orders/{rental['order_id']}").json()["order"]
    listed = owner.get(f"/api/c/{s.cmp}/orders", {"q": str(order["number"])}).json()["orders"]
    assert [o["id"] for o in listed] == [order["id"]]
    assert owner.get(f"/api/c/{s.cmp}/orders", {"q": "999999"}).json()["orders"] == []
    vehicles = owner.get(f"/api/c/{s.cmp}/vehicles", {"q": "toyota"}).json()["vehicles"]
    assert [v["id"] for v in vehicles] == [s.vehicle]
    assert owner.get(f"/api/c/{s.cmp}/vehicles", {"q": "bmw"}).json()["vehicles"] == []
    post(owner, f"/api/c/{s.cmp}/tasks", {"title": "Check tyres"}, key())
    tasks = owner.get(f"/api/c/{s.cmp}/tasks", {"view": "all", "q": "tyres"}).json()["tasks"]
    assert [t["title"] for t in tasks] == ["Check tyres"]


def test_search_widens_no_rights_and_stays_in_the_company(owner: Client) -> None:
    s = build(owner)
    rental = issue(owner, s).json()["rental"]
    number = owner.get(f"/api/c/{s.cmp}/orders/{rental['order_id']}").json()["order"]["number"]
    # § 2.8.2: an employee without the section rights finds nothing of them.
    plain, _ = hire(
        owner, s.cmp, "plain@example.com", [{"type": "grant", "permission": "vehicle.view"}]
    )
    found = _search(plain, s.cmp, str(number))
    assert "order" not in found and "client" not in found
    assert [h["id"] for h in _search(plain, s.cmp, "yaris")["vehicle"]] == [s.vehicle]
    assert "client" not in _search(plain, s.cmp, "Petrov")
    # § 2.8.1: another company of the same user finds none of these records.
    other = create_company(owner, key="c-2", display_name="Other").json()["company"]["public_id"]
    assert _search(owner, other, "yaris") == {}
    assert _search(owner, other, str(number)) == {}
    # A stranger cannot search in the company.
    stranger = Client()
    named_user(stranger, "x@example.com")
    assert stranger.get(f"/api/c/{s.cmp}/search", {"q": "yaris"}).status_code in (403, 404)
