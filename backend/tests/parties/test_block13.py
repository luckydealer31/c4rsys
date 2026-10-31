"""Block 13: clients and counterparties — archive, blacklist, extended cards, provisional cards,
merge and split of duplicates, the history in the card and fresh data from a linked profile
(ТЗ § 6.1.13–6.1.14, § 6.2.3, § 6.2.9–6.2.13, § 6.4.4, § 6.5.2–6.5.5, § 6.6.4–6.6.5, § 6.7.1,
§ 8.1.2, § 23.8.2, § 23.8.6–23.8.7)."""

from datetime import timedelta
from typing import Any

import pytest
from django.db import IntegrityError, transaction
from django.test import Client
from django.utils import timezone

from c4rsys.companies.models import PermissionSetItem
from c4rsys.finance.models import ChargeEntry
from c4rsys.orders.models import Order
from c4rsys.parties.models import (
    CardMerge,
    ClientCard,
    ClientRestriction,
    Counterparty,
    ProfileDataRequest,
)
from c4rsys.platform.models import AuditEvent
from tests.accounts.helpers import post
from tests.companies.helpers import create_company, hire, key, named_user
from tests.rentals.scenario import Scenario, add_client, build, issue_body

pytestmark = pytest.mark.django_db

NEW_RIGHTS = (
    "client.archive",
    "client.restore",
    "client.merge",
    "client.split",
    "client.blacklist.add",
    "client.blacklist.remove",
    "counterparty.merge",
    "counterparty.split",
)


@pytest.fixture
def s(owner: Client) -> Scenario:
    return build(owner)


def grant(*permissions: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "grant",
            "permission": p,
            "locations": "all",
            "contexts": "all",
            "cash_accounts": "all",
        }
        for p in permissions
    ]


def card(client: Client, cmp: str, card_id: str) -> dict[str, Any]:
    found: dict[str, Any] = client.get(f"/api/c/{cmp}/clients/{card_id}").json()["client"]
    return found


def person(
    owner: Client, cmp: str, first: str = "Ivan", last: str = "Petrov", **extra: Any
) -> dict[str, Any]:
    body = {
        "kind": "person",
        "first_name": first,
        "last_name": last,
        "contacts": [{"kind": "phone", "value": "+66 81 111 2222"}],
        **extra,
    }
    created = post(owner, f"/api/c/{cmp}/clients", body, key())
    assert created.status_code == 201, created.content
    found: dict[str, Any] = created.json()["client"]
    return found


def order(client: Client, cmp: str, client_id: str) -> Any:
    return post(client, f"/api/c/{cmp}/orders", {"client_id": client_id}, key())


def user_client(email: str) -> tuple[Client, str]:
    other = Client()
    named_user(other, email)
    public_id: str = other.get("/api/me").json()["user"]["public_id"]
    return other, public_id


def link_person(owner: Client, cmp: str, card_id: str, email: str) -> tuple[Client, str]:
    """A person accepts the request of the card (§ 6.2.5)."""
    person_client, usr = user_client(email)
    sent = post(
        owner,
        f"/api/c/{cmp}/profile-links",
        {"card_type": "client", "card_id": card_id, "target": usr},
        key(),
    )
    assert sent.status_code == 201, sent.content
    link_id: str = sent.json()["link"]["id"]
    accepted = post(
        person_client,
        f"/api/profile-links/{link_id}/accept",
        {"fields": ["first_name", "last_name", "email"]},
        key(),
    )
    assert accepted.status_code == 200, accepted.content
    return person_client, link_id


def compare(client: Client, cmp: str, primary: str, duplicate: str, kind: str = "client") -> Any:
    return client.get(
        f"/api/c/{cmp}/card-merges/preview?card_type={kind}&primary_id={primary}"
        f"&duplicate_id={duplicate}"
    )


def merge(
    client: Client,
    cmp: str,
    primary: str,
    duplicate: str,
    choices: dict[str, str] | None = None,
    kind: str = "client",
) -> Any:
    body = {
        "card_type": kind,
        "primary_id": primary,
        "duplicate_id": duplicate,
        "choices": choices or {},
        "reason": "same person",
    }
    return post(client, f"/api/c/{cmp}/card-merges", body, key())


# --- Rights (§ 23.8.2, § 23.8.6–23.8.7) ------------------------------------------------------


def test_new_rights_are_in_company_admin_only() -> None:
    admin = set(
        PermissionSetItem.objects.filter(permission_set__code="company_admin").values_list(
            "permission_id", flat=True
        )
    )
    manager = set(
        PermissionSetItem.objects.filter(permission_set__code="rental_manager").values_list(
            "permission_id", flat=True
        )
    )
    assert set(NEW_RIGHTS) <= admin
    assert not set(NEW_RIGHTS) & manager


# --- Archive (§ 6.6.4–6.6.5) ---------------------------------------------------------------------


def test_archive_hides_card_keeps_link_and_restore_is_separate(owner: Client, s: Scenario) -> None:
    client = card(owner, s.cmp, s.client)
    clerk, _ = hire(owner, s.cmp, "clerk@example.com", grant("client.list", "client.archive"))

    archived = post(
        clerk,
        f"/api/c/{s.cmp}/clients/{s.client}/archive",
        {"expected_version": client["version"]},
        key(),
    )
    assert archived.status_code == 200, archived.content
    assert archived.json()["client"]["state"] == "archived"
    listed = owner.get(f"/api/c/{s.cmp}/clients").json()["clients"]
    assert s.client not in [c["id"] for c in listed]
    archive = owner.get(f"/api/c/{s.cmp}/clients?state=archived").json()["clients"]
    assert [c["id"] for c in archive] == [s.client]
    # A new operation needs the card restored (PROPOSAL); the data and history stay.
    refused = order(owner, s.cmp, s.client)
    assert refused.json()["error"]["code"] == "CLIENT_ARCHIVED"

    version = archived.json()["client"]["version"]
    denied = post(
        clerk, f"/api/c/{s.cmp}/clients/{s.client}/restore", {"expected_version": version}, key()
    )
    assert denied.status_code == 403
    restored = post(
        owner, f"/api/c/{s.cmp}/clients/{s.client}/restore", {"expected_version": version}, key()
    )
    assert restored.json()["client"]["state"] == "active"
    assert order(owner, s.cmp, s.client).status_code == 201
    assert AuditEvent.objects.filter(action__in=["client.archived", "client.restored"]).count() == 2


def test_archived_card_stays_linked(owner: Client, s: Scenario) -> None:
    link_person(owner, s.cmp, s.client, "ivan@example.com")
    version = card(owner, s.cmp, s.client)["version"]
    post(owner, f"/api/c/{s.cmp}/clients/{s.client}/archive", {"expected_version": version}, key())
    assert ClientCard.objects.get(pk=s.client).linked_user_id is not None


# --- Blacklist (§ 6.7.1, § 23.8.7) ---------------------------------------------------------------


def test_blacklist_rights_reason_and_author(owner: Client, s: Scenario) -> None:
    adder, _ = hire(owner, s.cmp, "add@example.com", grant("client.find", "client.blacklist.add"))
    url = f"/api/c/{s.cmp}/clients/{s.client}/restrictions"

    assert post(adder, url, {"reason": " "}, key()).json()["error"]["code"] == "FIELD_REQUIRED"
    added = post(adder, url, {"reason": "Damaged two cars"}, key())
    assert added.status_code == 201, added.content
    mark = added.json()["restriction"]
    assert post(adder, url, {"reason": "again"}, key()).json()["error"]["code"] == (
        "CLIENT_ALREADY_BLACKLISTED"
    )
    lift = f"/api/c/{s.cmp}/client-restrictions/{mark['id']}/lift"
    # Setting and lifting are separate rights.
    assert post(adder, lift, {"reason": "paid"}, key()).status_code == 403
    assert card(owner, s.cmp, s.client)["blacklisted"] is True

    lifted = post(owner, lift, {"reason": "Paid the damage"}, key())
    assert lifted.status_code == 200, lifted.content
    history = adder.get(url).json()["restrictions"]
    assert history[0]["added_by"] == "Anna Petrova"
    assert history[0]["lifted_reason"] == "Paid the damage" and history[0]["lifted_at"]
    assert card(owner, s.cmp, s.client)["blacklisted"] is False
    with pytest.raises(IntegrityError), transaction.atomic():
        ClientRestriction.objects.create(
            company_id=ClientCard.objects.get(pk=s.client).company_id,
            client_id=s.client,
            reason="",
            added_by_user_id=ClientCard.objects.get(pk=s.client).created_by_user_id,
        )


def test_blacklist_forbids_new_orders_and_the_start_of_a_booking(
    owner: Client, s: Scenario
) -> None:
    start = timezone.now() + timedelta(hours=1)
    booked = post(
        owner,
        f"/api/c/{s.cmp}/bookings",
        {
            "vehicle_card_id": s.vehicle,
            "planned_start": start.isoformat(),
            "planned_end": (start + timedelta(days=2)).isoformat(),
            "client_id": s.client,
            "tariff_id": s.tariff,
            "issue_location_id": s.location,
            "place_kind": "location",
        },
        key(),
    )
    assert booked.status_code == 201, booked.content
    rental = booked.json()["rental"]
    post(owner, f"/api/c/{s.cmp}/clients/{s.client}/restrictions", {"reason": "fraud"}, key())

    assert order(owner, s.cmp, s.client).json()["error"]["code"] == "CLIENT_BLACKLISTED"
    body = issue_body(s, actual_start=(timezone.now() - timedelta(minutes=1)).isoformat())
    started = post(
        owner, f"/api/c/{s.cmp}/rentals/issue", {**body, "booking_id": rental["id"]}, key()
    )
    assert started.json()["error"]["code"] == "CLIENT_BLACKLISTED"
    # The earlier booking is not removed by itself.
    assert Order.objects.get(pk=rental["order_id"]).state == "open"


def test_blacklist_does_not_block_payments(owner: Client, s: Scenario) -> None:
    desk = post(
        owner,
        f"/api/c/{s.cmp}/cash-accounts",
        {"name": "Cash", "type": "cash", "currency": "THB"},
        key(),
    ).json()["cash_account"]["id"]
    post(owner, f"/api/c/{s.cmp}/clients/{s.client}/restrictions", {"reason": "debt"}, key())
    paid = post(
        owner,
        f"/api/c/{s.cmp}/payments/cash",
        {
            "cash_account_id": desk,
            "client_id": s.client,
            "amount": "500",
            "currency": "THB",
            "allocations": [],
        },
        key(),
    )
    assert paid.status_code == 201, paid.content


# --- Extended fields (§ 6.2.12–6.2.13) ---------------------------------------------------------


def test_person_card_with_documents_and_addresses(owner: Client, s: Scenario) -> None:
    created = person(
        owner,
        s.cmp,
        "Olga",
        "Smirnova",
        id_document={"type": "passport", "number": "75 1234567", "country": "ru"},
        driver_license={"number": "99 12 345678", "categories": ["b", "A", "B"]},
        driving_since="2010-06-01",
        other_documents=[{"title": "Insurance", "number": "INS-1"}],
        registration_address="Moscow",
        residence_address="Phuket",
    )
    assert created["id_document"]["country"] == "RU"
    assert created["driver_license"]["categories"] == ["B", "A"]
    assert created["driving_since"] == "2010-06-01"
    assert created["other_documents"][0]["title"] == "Insurance"
    assert created["residence_address"] == "Phuket"

    wrong = post(
        owner,
        f"/api/c/{s.cmp}/clients",
        {
            "kind": "person",
            "first_name": "A",
            "last_name": "B",
            "contacts": [{"kind": "phone", "value": "+66811111111"}],
            "tax_number": "123",
        },
        key(),
    )
    assert wrong.json()["error"]["code"] == "FIELD_NOT_APPLICABLE"
    bad = owner.patch(
        f"/api/c/{s.cmp}/clients/{created['id']}",
        data={"expected_version": created["version"], "id_document": {"type": "visa"}},
        content_type="application/json",
        headers=key(),
    )
    assert bad.json()["error"]["field"] == "id_document.type"


def test_company_card_with_requisites(owner: Client, s: Scenario) -> None:
    created = post(
        owner,
        f"/api/c/{s.cmp}/clients",
        {
            "kind": "company",
            "company_name": "Andaman Tours",
            "contacts": [{"kind": "email", "value": "a@andaman.example"}],
            "country": "th",
            "legal_form": "Co., Ltd.",
            "registration_number": "0105551234567",
            "tax_number": "0105551234567",
            "legal_address": "Bangkok",
            "actual_address": "Phuket",
            "director_name": "S. K.",
            "contact_person": "N. P.",
            "bank_details": "KBank 123",
            "signatory_name": "S. K.",
            "signatory_basis": "Articles",
        },
        key(),
    )
    assert created.status_code == 201, created.content
    found = created.json()["client"]
    assert found["country"] == "TH" and found["signatory_basis"] == "Articles"
    assert found["id_document"] is None


def test_counterparty_has_the_extended_fields(owner: Client, s: Scenario) -> None:
    created = post(
        owner,
        f"/api/c/{s.cmp}/counterparties",
        {
            "kind": "company",
            "company_name": "Parts Co",
            "contacts": [{"kind": "phone", "value": "+66810000001"}],
            "tax_number": "T-1",
            "bank_details": "SCB 1",
        },
        key(),
    )
    assert created.status_code == 201, created.content
    assert created.json()["counterparty"]["tax_number"] == "T-1"


# --- Provisional card (§ 6.2.9–6.2.11) ---------------------------------------------------------


def test_provisional_card_waits_for_the_link_and_is_not_ready(owner: Client, s: Scenario) -> None:
    somchai, _usr = user_client("somchai@example.com")
    created = post(
        owner,
        f"/api/c/{s.cmp}/clients/provisional",
        {"kind": "person", "target": "Somchai@Example.com"},
        key(),
    )
    assert created.status_code == 201, created.content
    client = created.json()["client"]
    assert client["provisional"] is True and client["first_name"] == ""
    assert client["name"] == "somchai@example.com"
    assert created.json()["link"]["state"] == "pending"
    # § 6.2.10: not ready for operations, even after the answer, until the data are there.
    assert order(owner, s.cmp, client["id"]).json()["error"]["code"] == "CLIENT_PROVISIONAL"

    link_id = created.json()["link"]["id"]
    post(
        somchai,
        f"/api/profile-links/{link_id}/accept",
        {"fields": ["first_name", "last_name"]},
        key(),
    )
    assert order(owner, s.cmp, client["id"]).json()["error"]["code"] == "CLIENT_PROVISIONAL"
    version = card(owner, s.cmp, client["id"])["version"]
    for field in ("first_name", "last_name"):
        applied = post(
            owner,
            f"/api/c/{s.cmp}/profile-links/{link_id}/apply",
            {"field": field, "expected_version": version},
            key(),
        )
        assert applied.status_code == 200, applied.content
        version = applied.json()["version"]
    ready = card(owner, s.cmp, client["id"])
    assert ready["provisional"] is False and ready["last_name"] == "Petrova"
    assert order(owner, s.cmp, client["id"]).status_code == 201


def test_provisional_card_needs_create_and_link_rights(owner: Client, s: Scenario) -> None:
    creator, _ = hire(owner, s.cmp, "c@example.com", grant("client.create"))
    refused = post(
        creator,
        f"/api/c/{s.cmp}/clients/provisional",
        {"kind": "person", "target": "x@example.com"},
        key(),
    )
    assert refused.status_code == 403
    with pytest.raises(IntegrityError), transaction.atomic():
        ClientCard.objects.create(
            company_id=ClientCard.objects.get(pk=s.client).company_id,
            kind="person",
            created_by_user_id=ClientCard.objects.get(pk=s.client).created_by_user_id,
        )


def test_provisional_counterparty_by_company_id(owner: Client, s: Scenario) -> None:
    other = Client()
    named_user(other, "supplier@example.com")
    cmp_b = create_company(other, display_name="Parts Supplier").json()["company"]["public_id"]
    created = post(
        owner,
        f"/api/c/{s.cmp}/counterparties/provisional",
        {"kind": "company", "target": cmp_b},
        key(),
    )
    assert created.status_code == 201, created.content
    assert created.json()["counterparty"]["provisional"] is True
    assert Counterparty.objects.get(pk=created.json()["counterparty"]["id"]).company_name == ""


# --- Merge and split (§ 6.2.3, § 6.5.2–6.5.5) ---------------------------------------------------


def test_merge_compares_requires_choices_and_redirects(owner: Client, s: Scenario) -> None:
    dup = person(owner, s.cmp, "Ivan", "Petroff", residence_address="Patong")
    main = card(owner, s.cmp, s.client)
    compared = compare(owner, s.cmp, s.client, dup["id"])
    assert compared.status_code == 200, compared.content
    fields = {f["key"]: f for f in compared.json()["comparison"]["fields"]}
    assert fields["last_name"]["same"] is False and fields["first_name"]["same"] is True
    assert fields["contacts"]["choices"] == ["primary", "duplicate", "both"]
    assert compared.json()["comparison"]["obstacles"] == []

    missing = merge(owner, s.cmp, s.client, dup["id"], {"last_name": "primary"})
    assert missing.json()["error"]["code"] == "CARD_MERGE_CHOICE_REQUIRED"
    done = merge(
        owner,
        s.cmp,
        s.client,
        dup["id"],
        {"last_name": "primary", "residence_address": "duplicate", "contacts": "both"},
    )
    assert done.status_code == 201, done.content
    merged = card(owner, s.cmp, s.client)
    assert merged["last_name"] == "Petrov" and merged["residence_address"] == "Patong"
    assert {c["value"] for c in merged["contacts"]} == {
        main["contacts"][0]["value"],
        "+66 81 111 2222",
    }
    duplicate = card(owner, s.cmp, dup["id"])
    assert duplicate["state"] == "merged" and duplicate["merged_into"] == s.client
    # The duplicate keeps its own data (§ 6.5.2) and is closed for new operations.
    assert duplicate["last_name"] == "Petroff"
    refused = order(owner, s.cmp, dup["id"])
    assert refused.json()["error"]["code"] == "CLIENT_MERGED"
    assert refused.json()["error"]["params"]["main"] == s.client
    listed = owner.get(f"/api/c/{s.cmp}/clients").json()["clients"]
    assert dup["id"] not in [c["id"] for c in listed]
    matches = post(owner, f"/api/c/{s.cmp}/clients/matches", {"phone": "+66 81 111 2222"})
    assert [m["client"]["id"] for m in matches.json()["matches"]] == [s.client]
    assert AuditEvent.objects.filter(action="client.merged").count() == 2


def test_merge_shows_the_duplicate_history_in_the_main_card(owner: Client, s: Scenario) -> None:
    dup = person(owner, s.cmp, "Ivan", "Petrov")
    old = order(owner, s.cmp, dup["id"]).json()["order"]
    post(
        owner,
        f"/api/c/{s.cmp}/orders/{old['id']}/cancel",
        {"expected_version": old["version"], "reason": "test"},
        key(),
    )
    done = merge(owner, s.cmp, s.client, dup["id"], {"contacts": "primary"})
    assert done.status_code == 201, done.content
    history = owner.get(f"/api/c/{s.cmp}/clients/{s.client}/history").json()
    assert old["id"] in [o["id"] for o in history["orders"]]
    assert set(history["cards"]) == {s.client, dup["id"]}
    # Not rewritten: the order still names the duplicate.
    assert str(Order.objects.get(pk=old["id"]).client_id) == dup["id"]
    filtered = owner.get(f"/api/c/{s.cmp}/orders?client_id={s.client}").json()["orders"]
    assert old["id"] in [o["id"] for o in filtered]


def test_merge_obstacles(owner: Client, s: Scenario) -> None:
    dup = person(owner, s.cmp, "Ivan", "Petrov")
    assert order(owner, s.cmp, dup["id"]).status_code == 201
    blocked = merge(owner, s.cmp, s.client, dup["id"], {"contacts": "primary"})
    assert blocked.json()["error"]["code"] == "CARD_MERGE_NOT_ALLOWED"
    assert "duplicate_has_open_orders" in blocked.json()["error"]["params"]["obstacles"]

    company = post(
        owner,
        f"/api/c/{s.cmp}/clients",
        {
            "kind": "company",
            "company_name": "X",
            "contacts": [{"kind": "email", "value": "x@x.example"}],
        },
        key(),
    ).json()["client"]
    kinds = compare(owner, s.cmp, s.client, company["id"]).json()["comparison"]["obstacles"]
    assert "kind_differs" in kinds


def test_merge_of_cards_linked_with_different_profiles_needs_a_check(
    owner: Client, s: Scenario
) -> None:
    dup = person(owner, s.cmp, "Ivan", "Petrov")
    link_person(owner, s.cmp, s.client, "one@example.com")
    link_person(owner, s.cmp, dup["id"], "two@example.com")
    obstacles = compare(owner, s.cmp, s.client, dup["id"]).json()["comparison"]["obstacles"]
    assert "different_profiles" in obstacles


def test_merge_needs_its_right(owner: Client, s: Scenario) -> None:
    dup = person(owner, s.cmp)
    clerk, _ = hire(owner, s.cmp, "m@example.com", grant("client.list", "client.edit"))
    assert compare(clerk, s.cmp, s.client, dup["id"]).status_code == 403
    assert merge(clerk, s.cmp, s.client, dup["id"]).status_code == 403


def test_linked_duplicate_gives_its_link_and_split_returns_it(owner: Client, s: Scenario) -> None:
    dup = person(owner, s.cmp, "Ivan", "Petrov")
    ivan, link_id = link_person(owner, s.cmp, dup["id"], "ivan@example.com")
    main_order = order(owner, s.cmp, s.client).json()["order"]
    post(
        owner,
        f"/api/c/{s.cmp}/orders/{main_order['id']}/cancel",
        {"expected_version": main_order["version"], "reason": "test"},
        key(),
    )
    done = merge(owner, s.cmp, s.client, dup["id"], {"contacts": "primary"})
    assert done.status_code == 201, done.content
    assert ClientCard.objects.get(pk=s.client).linked_user_id is not None
    assert ClientCard.objects.get(pk=dup["id"]).linked_user_id is None
    # § 6.3.6: the profile sees the merged history.
    shown = ivan.get(f"/api/cabinet/companies/{link_id}/history")
    assert main_order["id"] in [o["id"] for o in shown.json()["orders"]]

    merge_id = done.json()["merge"]["id"]
    after = order(owner, s.cmp, s.client).json()["order"]
    preview = owner.get(f"/api/c/{s.cmp}/card-merges/{merge_id}/split")
    assert preview.status_code == 200, preview.content
    keys = [r["key"] for r in preview.json()["records"]]
    assert keys == [f"order:{after['id']}"]
    unassigned = post(
        owner, f"/api/c/{s.cmp}/card-merges/{merge_id}/split", {"reason": "mistake"}, key()
    )
    assert unassigned.json()["error"]["code"] == "CARD_SPLIT_UNASSIGNED"
    split = post(
        owner,
        f"/api/c/{s.cmp}/card-merges/{merge_id}/split",
        {"reason": "mistake", "assignments": {keys[0]: "duplicate"}},
        key(),
    )
    assert split.status_code == 200, split.content
    assert split.json()["merge"]["state"] == "split"
    assert split.json()["merge"]["revoked"]
    assert str(Order.objects.get(pk=after["id"]).client_id) == dup["id"]
    assert ClientCard.objects.get(pk=dup["id"]).state == "active"
    assert ClientCard.objects.get(pk=dup["id"]).linked_user_id is not None
    assert ClientCard.objects.get(pk=s.client).linked_user_id is None
    # § 6.5.5: the history of the other card is no longer shown.
    shown = ivan.get(f"/api/cabinet/companies/{link_id}/history")
    ids = [o["id"] for o in shown.json()["orders"]]
    assert main_order["id"] not in ids and after["id"] in ids
    assert AuditEvent.objects.filter(action="order.client_moved").count() == 1


def test_split_restores_values_and_needs_elevated_right(owner: Client, s: Scenario) -> None:
    dup = person(owner, s.cmp, "Ivan", "Petroff")
    done = merge(owner, s.cmp, s.client, dup["id"], {"last_name": "duplicate", "contacts": "both"})
    merge_id = done.json()["merge"]["id"]
    assert card(owner, s.cmp, s.client)["last_name"] == "Petroff"
    clerk, _ = hire(owner, s.cmp, "x@example.com", grant("client.list", "client.merge"))
    assert owner.get(f"/api/c/{s.cmp}/card-merges/{merge_id}/split").json()["records"] == []
    denied = post(clerk, f"/api/c/{s.cmp}/card-merges/{merge_id}/split", {"reason": "x"}, key())
    assert denied.status_code == 403
    split = post(owner, f"/api/c/{s.cmp}/card-merges/{merge_id}/split", {"reason": "x"}, key())
    assert split.status_code == 200, split.content
    restored = card(owner, s.cmp, s.client)
    assert restored["last_name"] == "Petrov" and len(restored["contacts"]) == 1
    merges = owner.get(f"/api/c/{s.cmp}/card-merges?card_type=client&card_id={s.client}")
    assert [m["state"] for m in merges.json()["merges"]] == ["split"]
    assert CardMerge.objects.get().split_reason == "x"


def test_split_moves_charges_of_an_order_by_reversing_entries(owner: Client, s: Scenario) -> None:
    dup_id = add_client(owner, s.cmp, "Ivan", "+66899999999")
    done = merge(owner, s.cmp, s.client, dup_id, {"contacts": "primary"})
    merge_id = done.json()["merge"]["id"]
    body = issue_body(s, actual_start=(timezone.now() - timedelta(minutes=1)).isoformat())
    issued = post(owner, f"/api/c/{s.cmp}/rentals/issue", body, key())
    assert issued.status_code == 201, issued.content
    rental = issued.json()["rental"]
    returned = post(
        owner,
        f"/api/c/{s.cmp}/rentals/{rental['id']}/return",
        {
            "actual_end": timezone.now().isoformat(),
            "return_location_id": s.location,
            "odometer": "10100",
            "fuel": "8",
            "expected_version": rental["version"],
        },
        key(),
    )
    assert returned.status_code == 200, returned.content
    assert ChargeEntry.objects.filter(order_id=rental["order_id"]).exists()
    records = owner.get(f"/api/c/{s.cmp}/card-merges/{merge_id}/split").json()["records"]
    split = post(
        owner,
        f"/api/c/{s.cmp}/card-merges/{merge_id}/split",
        {"reason": "wrong", "assignments": {r["key"]: "duplicate" for r in records}},
        key(),
    )
    assert split.status_code == 200, split.content
    entries = ChargeEntry.objects.filter(order_id=rental["order_id"])
    by_client: dict[str, Any] = {}
    for e in entries:
        by_client[str(e.client_id)] = by_client.get(str(e.client_id), 0) + e.amount
    assert by_client[s.client] == 0
    assert by_client[dup_id] > 0
    from c4rsys.rentals.models import Rental

    assert str(Rental.objects.get(pk=rental["id"]).client_id) == dup_id


def test_counterparty_merge(owner: Client, s: Scenario) -> None:
    def supplier(name: str) -> str:
        created = post(
            owner,
            f"/api/c/{s.cmp}/counterparties",
            {
                "kind": "company",
                "company_name": name,
                "contacts": [{"kind": "phone", "value": "+66810000001"}],
            },
            key(),
        )
        found: str = created.json()["counterparty"]["id"]
        return found

    main, dup = supplier("Parts Co"), supplier("Parts Co.")
    done = merge(owner, s.cmp, main, dup, {"company_name": "primary"}, kind="counterparty")
    assert done.status_code == 201, done.content
    assert Counterparty.objects.get(pk=dup).state == "merged"
    listed = owner.get(f"/api/c/{s.cmp}/counterparties?state=").json()["counterparties"]
    assert dup not in [c["id"] for c in listed]


# --- Revoking wrongly shown history (§ 6.5.5) ----------------------------------------------------


def test_break_with_revoke_hides_history_and_needs_reason(owner: Client, s: Scenario) -> None:
    ivan, link_id = link_person(owner, s.cmp, s.client, "ivan@example.com")
    url = f"/api/c/{s.cmp}/profile-links/{link_id}/break"
    no_reason = post(owner, url, {"initiator": "company", "revoke": True}, key())
    assert no_reason.json()["error"]["code"] == "FIELD_REQUIRED"
    editor, _ = hire(owner, s.cmp, "e@example.com", grant("client.find", "client.edit"))
    denied = post(editor, url, {"initiator": "company", "revoke": True, "reason": "x"}, key())
    assert denied.status_code == 403
    done = post(
        owner, url, {"initiator": "company", "revoke": True, "reason": "Wrong person"}, key()
    )
    assert done.status_code == 200, done.content
    assert done.json()["link"]["access_revoke_reason"] == "Wrong person"
    from c4rsys.parties import links

    assert links.client_cards_of_user(ClientCard.objects.get(pk=s.client).created_by_user_id) == []
    assert AuditEvent.objects.filter(action="profile_link.access_revoked").count() == 1


# --- Fresh data from the linked profile (§ 6.4.4) -------------------------------------------------


def test_fresh_data_request_answered_in_the_cabinet(owner: Client, s: Scenario) -> None:
    ivan, link_id = link_person(owner, s.cmp, s.client, "ivan@example.com")
    url = f"/api/c/{s.cmp}/profile-links/{link_id}/data-requests"
    no_occasion = post(owner, url, {"occasion": ""}, key())
    assert no_occasion.status_code == 400
    sent = post(owner, url, {"occasion": "Rents a car today"}, key())
    assert sent.status_code == 201, sent.content
    assert post(owner, url, {"occasion": "again"}, key()).json()["error"]["code"] == (
        "DATA_REQUEST_PENDING"
    )
    mine = ivan.get("/api/profile-data-requests").json()["requests"]
    assert mine[0]["occasion"] == "Rents a car today" and mine[0]["state"] == "pending"
    accepted = post(
        ivan,
        f"/api/profile-data-requests/{mine[0]['id']}/accept",
        {"fields": ["last_name"]},
        key(),
    )
    assert accepted.status_code == 200, accepted.content
    answered = owner.get(url).json()["requests"][0]
    # The request does not open every field (§ 6.4.4).
    assert answered["state"] == "accepted" and answered["shared"] == {"last_name": "Petrova"}
    assert card(owner, s.cmp, s.client)["last_name"] == "Petrov"
    version = card(owner, s.cmp, s.client)["version"]
    applied = post(
        owner,
        f"/api/c/{s.cmp}/profile-links/{link_id}/apply",
        {"field": "last_name", "expected_version": version, "data_request_id": answered["id"]},
        key(),
    )
    assert applied.status_code == 200, applied.content
    assert card(owner, s.cmp, s.client)["last_name"] == "Petrova"


def test_fresh_data_needs_a_confirmed_link(owner: Client, s: Scenario) -> None:
    _user, usr = user_client("pending@example.com")
    sent = post(
        owner,
        f"/api/c/{s.cmp}/profile-links",
        {"card_type": "client", "card_id": s.client, "target": usr},
        key(),
    ).json()["link"]
    refused = post(
        owner,
        f"/api/c/{s.cmp}/profile-links/{sent['id']}/data-requests",
        {"occasion": "service"},
        key(),
    )
    assert refused.json()["error"]["code"] == "LINK_NOT_ACCEPTED"
    assert ProfileDataRequest.objects.count() == 0


def test_fresh_data_declined(owner: Client, s: Scenario) -> None:
    ivan, link_id = link_person(owner, s.cmp, s.client, "ivan@example.com")
    sent = post(
        owner,
        f"/api/c/{s.cmp}/profile-links/{link_id}/data-requests",
        {"occasion": "service"},
        key(),
    ).json()["request"]
    declined = post(ivan, f"/api/profile-data-requests/{sent['id']}/decline", {}, key())
    assert declined.json()["request"]["state"] == "declined"


# --- History in the cards (§ 6.1.14, § 8.1.2) ----------------------------------------------------


def test_client_history_lists_orders_payments_and_vehicles(owner: Client, s: Scenario) -> None:
    body = issue_body(s, actual_start=(timezone.now() - timedelta(minutes=1)).isoformat())
    issued = post(owner, f"/api/c/{s.cmp}/rentals/issue", body, key())
    assert issued.status_code == 201, issued.content
    history = owner.get(f"/api/c/{s.cmp}/clients/{s.client}/history").json()
    assert [o["id"] for o in history["orders"]] == [issued.json()["rental"]["order_id"]]
    assert history["orders"][0]["operations"][0]["type"] == "rental"
    assert [v["id"] for v in history["vehicles"]] == [s.vehicle]
    assert history["vehicles"][0]["sources"] == ["rental"]

    finder, _ = hire(owner, s.cmp, "f@example.com", grant("client.find"))
    limited = finder.get(f"/api/c/{s.cmp}/clients/{s.client}/history").json()
    # Within the rights of the employee: no orders or vehicles without their rights.
    assert limited["orders"] == [] and limited["vehicles"] == []


def test_customer_company_sees_contractor_history(owner: Client, s: Scenario) -> None:
    # Company B is a client of the rental company (s.cmp) and has it as a counterparty.
    other = Client()
    named_user(other, "b@example.com")
    cmp_b = create_company(other, display_name="Customer B").json()["company"]["public_id"]
    client_b = post(
        owner,
        f"/api/c/{s.cmp}/clients",
        {
            "kind": "company",
            "company_name": "Customer B",
            "contacts": [{"kind": "email", "value": "b@b.example"}],
        },
        key(),
    ).json()["client"]
    sent = post(
        owner,
        f"/api/c/{s.cmp}/profile-links",
        {"card_type": "client", "card_id": client_b["id"], "target": cmp_b},
        key(),
    ).json()["link"]
    assert (
        post(
            other,
            f"/api/c/{cmp_b}/incoming-links/{sent['id']}/accept",
            {"fields": ["display_name"]},
            key(),
        ).status_code
        == 200
    )
    made = order(owner, s.cmp, client_b["id"]).json()["order"]
    contractor = post(
        other,
        f"/api/c/{cmp_b}/counterparties",
        {
            "kind": "company",
            "company_name": "Rent",
            "contacts": [{"kind": "phone", "value": "+66810000009"}],
        },
        key(),
    ).json()["counterparty"]
    history = other.get(f"/api/c/{cmp_b}/counterparties/{contractor['id']}/history")
    assert history.json()["orders"] == []  # not linked with the contractor yet
    back = post(
        other,
        f"/api/c/{cmp_b}/profile-links",
        {"card_type": "counterparty", "card_id": contractor["id"], "target": s.cmp},
        key(),
    ).json()["link"]
    post(owner, f"/api/c/{s.cmp}/incoming-links/{back['id']}/accept", {"fields": []}, key())
    history = other.get(f"/api/c/{cmp_b}/counterparties/{contractor['id']}/history")
    assert [o["id"] for o in history.json()["orders"]] == [made["id"]]


def test_company_profile_answers_a_fresh_data_request(owner: Client, s: Scenario) -> None:
    from c4rsys.notifications.models import Notification
    from c4rsys.parties import notices

    other = Client()
    named_user(other, "b2@example.com")
    cmp_b = create_company(other, display_name="Customer B").json()["company"]["public_id"]
    client_b = post(
        owner,
        f"/api/c/{s.cmp}/clients",
        {
            "kind": "company",
            "company_name": "Customer B",
            "contacts": [{"kind": "email", "value": "b@b.example"}],
        },
        key(),
    ).json()["client"]
    link = post(
        owner,
        f"/api/c/{s.cmp}/profile-links",
        {"card_type": "client", "card_id": client_b["id"], "target": cmp_b},
        key(),
    ).json()["link"]
    post(other, f"/api/c/{cmp_b}/incoming-links/{link['id']}/accept", {"fields": []}, key())
    sent = post(
        owner,
        f"/api/c/{s.cmp}/profile-links/{link['id']}/data-requests",
        {"occasion": "Fleet service"},
        key(),
    ).json()["request"]
    notices.requests(timezone.now())
    assert Notification.objects.filter(event="link.data_requested").count() == 1
    incoming = other.get(f"/api/c/{cmp_b}/incoming-data-requests").json()["requests"]
    assert [r["id"] for r in incoming] == [sent["id"]]
    answered = post(
        other,
        f"/api/c/{cmp_b}/incoming-data-requests/{sent['id']}/accept",
        {"fields": ["display_name"]},
        key(),
    )
    assert answered.status_code == 200, answered.content
    notices.requests(timezone.now())
    assert not Notification.objects.filter(event="link.data_requested", state="active").exists()
    entry = AuditEvent.objects.get(action="profile_data_request.accepted")
    assert notices.on_data_answer(entry) == 1
    assert Notification.objects.get(event="link.data_answered").params["accepted"] is True
