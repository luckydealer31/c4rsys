"""Owners, share transfers, corporate ownership, succession, cycles (ТЗ § 3.3–3.5)."""

from collections.abc import Iterator
from decimal import Decimal
from typing import Any
from unittest import mock

import pytest
from django.test import Client

from c4rsys.companies.models import Employment, OwnershipShare, ShareTransfer
from tests.accounts.helpers import CODE, post
from tests.companies.helpers import create_company, hire, key, named_user

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def no_code_limits() -> Iterator[None]:
    """Several confirmations in one test; the limits of § 2.4.4 are tested in accounts."""
    with mock.patch("c4rsys.accounts.otp.check_send_allowed"):
        yield


def user(email: str) -> tuple[Client, str]:
    client = Client()
    named_user(client, email)
    return client, client.get("/api/me").json()["user"]["public_id"]


def company(client: Client, name: str = "Sunrise Rent") -> str:
    response = create_company(client, key()["Idempotency-Key"], display_name=name)
    assert response.status_code == 201, response.content
    public_id: str = response.json()["company"]["public_id"]
    return public_id


def code_flow(client: Client, action: str = "transfer_share") -> str:
    flow: str = post(client, "/api/me/confirmations", {"action": action}).json()["flow"]
    return flow


def transfer(
    client: Client, cmp: str, recipient: str, percent: str, path: str = "", **extra: Any
) -> Any:
    body = {
        "recipient": recipient,
        "percent": percent,
        "flow": code_flow(client),
        "code": CODE,
        **extra,
    }
    return post(client, path or f"/api/c/{cmp}/owners/transfers", body, key())


def started(response: Any) -> dict[str, Any]:
    assert response.status_code == 201, response.content
    result: dict[str, Any] = response.json()["transfer"]
    return result


def pending(client: Client) -> dict[str, Any]:
    body: dict[str, Any] = client.get("/api/ownership-requests").json()
    return body


def answer(client: Client, transfer_id: str, approve: bool = True, role: str = "") -> Any:
    items = [
        x
        for x in pending(client)["transfers"]
        if x["transfer"]["id"] == transfer_id and (not role or x["role"] == role)
    ]
    assert items, pending(client)
    verb = "approve" if approve else "decline"
    return post(
        client, f"/api/ownership-requests/transfers/{items[0]['approval_id']}/{verb}", {}, key()
    )


def shares(cmp: str) -> dict[str, Decimal]:
    result = {}
    for s in OwnershipShare.objects.filter(company__public_id=cmp, valid_to__isnull=True):
        owner = s.owner_company.public_id if s.owner_company else s.owner_user.public_id  # type: ignore[union-attr]
        result[owner] = s.percent
    assert sum(result.values()) == Decimal(100)
    return result


def code(response: Any) -> str:
    result: str = response.json()["error"]["code"]
    return result


@pytest.fixture
def a(owner: Client) -> tuple[Client, str]:
    return owner, owner.get("/api/me").json()["user"]["public_id"]


@pytest.fixture
def cmp(owner: Client) -> str:
    return company(owner)


# --- Owners view (§ 3.3.4, § 3.3.6) --------------------------------------------------------------


def test_creator_owns_100_and_sees_the_owners(a: tuple[Client, str], cmp: str) -> None:
    client, me = a
    body = client.get(f"/api/c/{cmp}/owners").json()
    assert [(o["owner"]["public_id"], o["percent"]) for o in body["owners"]] == [(me, "100.0000")]
    assert body["owners"][0]["owner"]["first_name"] == "Anna"
    assert body["owners"][0]["is_top_authority"] and body["me"]["is_owner"]
    assert len(body["history"]) == 1 and body["policy"]["transfer_mode"] == "none"


def test_owners_are_hidden_from_a_plain_employee(a: tuple[Client, str], cmp: str) -> None:
    worker, _ = hire(a[0], cmp, "worker@example.com", [{"type": "set", "set": "company_admin"}])
    # «Company administrator» gives no owner rights (§ 23.2.1).
    assert worker.get(f"/api/c/{cmp}/owners").status_code == 403
    assert code(transfer(worker, cmp, "USR-00000000", "1")) == "PERMISSION_DENIED"


# --- Transfer to a user (§ 3.4.3) -----------------------------------------------------------------


def test_transfer_needs_a_code_and_at_most_own_share(a: tuple[Client, str], cmp: str) -> None:
    client, _ = a
    _b, b_id = user("b@example.com")
    body = {"recipient": b_id, "percent": "10", "flow": code_flow(client), "code": "000000"}
    assert code(post(client, f"/api/c/{cmp}/owners/transfers", body, key())) == "CODE_INVALID"
    assert code(transfer(client, cmp, b_id, "100.5")) == "PERCENT_INVALID"
    assert code(transfer(client, cmp, b_id, "10.00001")) == "PERCENT_TOO_PRECISE"
    assert code(transfer(client, cmp, "nobody_here", "10")) == "OWNERSHIP_PARTY_NOT_FOUND"
    assert code(transfer(client, cmp, a[1], "10")) == "TRANSFER_RECIPIENT_INVALID"
    assert ShareTransfer.objects.count() == 0


def test_share_moves_only_after_the_recipient_accepts(a: tuple[Client, str], cmp: str) -> None:
    client, me = a
    b, b_id = user("b@example.com")
    t = started(transfer(client, cmp, b_id, "30"))
    # Before acceptance the sender sees the recipient by ID only (§ 2.2.4, § 3.3.6).
    assert "first_name" not in t["recipient"]
    assert shares(cmp) == {me: Decimal(100)}
    assert b.get(f"/api/c/{cmp}").status_code == 404

    assert answer(b, t["id"]).status_code == 200
    assert shares(cmp) == {me: Decimal(70), b_id: Decimal(30)}
    # The recipient's dedicated position: an employment without working rights (§ 3.4.3).
    body = b.get(f"/api/c/{cmp}").json()
    assert body["company"]["employment"]["position_code"] == "owner"
    assert body["permissions"] == []
    owners = client.get(f"/api/c/{cmp}/owners").json()
    assert {o["owner"]["public_id"]: o["owner"]["first_name"] for o in owners["owners"]} == {
        me: "Anna",
        b_id: "Anna",
    }
    assert len(owners["history"]) == 3  # 100 closed, 70 and 30 opened
    # The share gives owner rights: the new co-owner sees the owners too (§ 3.3.4).
    assert b.get(f"/api/c/{cmp}/owners").status_code == 200

    # A transfer over the remaining own share is refused (§ 3.4.3).
    _c, c_id = user("c@example.com")
    assert code(transfer(client, cmp, c_id, "70.0001")) == "TRANSFER_EXCEEDS_SHARE"


def test_decline_and_cancel_keep_the_shares(a: tuple[Client, str], cmp: str) -> None:
    client, me = a
    b, b_id = user("b@example.com")
    t = started(transfer(client, cmp, b_id, "30"))
    assert answer(b, t["id"], approve=False).status_code == 200
    assert ShareTransfer.objects.get(pk=t["id"]).state == "declined"
    t = started(transfer(client, cmp, b_id, "20"))
    assert post(client, f"/api/ownership-transfers/{t['id']}/cancel", {}, key()).status_code == 200
    assert ShareTransfer.objects.get(pk=t["id"]).state == "cancelled"
    assert shares(cmp) == {me: Decimal(100)}


def test_changed_conditions_replace_the_pending_request(a: tuple[Client, str], cmp: str) -> None:
    client, _ = a
    _b, b_id = user("b@example.com")
    first = started(transfer(client, cmp, b_id, "30"))
    same = started(transfer(client, cmp, b_id, "30"))
    assert same["id"] == first["id"]  # a reminder (§ 2.5.2)
    changed = started(transfer(client, cmp, b_id, "25"))
    assert changed["id"] != first["id"]
    assert ShareTransfer.objects.get(pk=first["id"]).state == "cancelled"


def _two_owners(client: Client, cmp: str, b_email: str = "b@example.com") -> tuple[Client, str]:
    b, b_id = user(b_email)
    t = started(transfer(client, cmp, b_id, "30"))
    assert answer(b, t["id"]).status_code == 200
    return b, b_id


def _policy(client: Client, cmp: str, **values: Any) -> Any:
    current = client.get(f"/api/c/{cmp}/ownership-policy").json()["policy"]
    body = {
        "transfer_mode": current["transfer_mode"],
        "transfer_percent": current["transfer_percent"],
        "state_mode": current["state_mode"],
        "state_percent": current["state_percent"],
        "expected_version": current["version"],
        **values,
    }
    return client.put(
        f"/api/c/{cmp}/ownership-policy",
        data=body,
        content_type="application/json",
        headers=key(),
    )


def test_policy_all_coowners_must_approve(a: tuple[Client, str], cmp: str) -> None:
    client, me = a
    b, b_id = _two_owners(client, cmp)
    # Only the top authority changes the policy (PROPOSAL).
    assert _policy(b, cmp, transfer_mode="all").status_code == 403
    assert _policy(client, cmp, transfer_mode="all").status_code == 200
    c, c_id = user("c@example.com")
    t = started(transfer(client, cmp, c_id, "10"))
    assert answer(c, t["id"]).status_code == 200
    assert shares(cmp)[me] == Decimal(70)  # waits for the co-owner (§ 3.4.2)
    assert answer(b, t["id"], role="coowner").status_code == 200
    assert shares(cmp) == {me: Decimal(60), b_id: Decimal(30), c_id: Decimal(10)}


def test_co_owner_is_notified_until_he_answers(a: tuple[Client, str], cmp: str) -> None:
    from django.utils import timezone

    from c4rsys.companies import ownership_notices
    from c4rsys.notifications.models import Notification, NotificationRecipient

    client, _ = a
    b, b_id = _two_owners(client, cmp)
    _policy(client, cmp, transfer_mode="all")
    c, c_id = user("c@example.com")
    t = started(transfer(client, cmp, c_id, "10"))
    ownership_notices.run(timezone.now())
    b_employment = Employment.objects.get(company__public_id=cmp, user__public_id=b_id)
    active = Notification.objects.filter(
        event="ownership.approval_requested", state=Notification.State.ACTIVE
    )
    assert NotificationRecipient.objects.filter(
        notification__in=active, employment=b_employment
    ).exists()
    answer(b, t["id"], role="coowner")
    ownership_notices.run(timezone.now())
    assert not NotificationRecipient.objects.filter(
        notification__in=active.all(), employment=b_employment
    ).exists()


def test_policy_percent_counts_the_sender(a: tuple[Client, str], cmp: str) -> None:
    client, me = a
    b, b_id = _two_owners(client, cmp)
    assert _policy(client, cmp, transfer_mode="percent", transfer_percent="80").status_code == 200
    c, c_id = user("c@example.com")
    t = started(transfer(client, cmp, c_id, "10"))
    assert answer(c, t["id"]).status_code == 200
    assert shares(cmp)[me] == Decimal(70)  # 70% < 80%
    assert answer(b, t["id"], role="coowner").status_code == 200
    assert shares(cmp)[c_id] == Decimal(10)
    assert _policy(client, cmp, transfer_mode="percent", transfer_percent="60").status_code == 200
    d, d_id = user("d@example.com")
    t = started(transfer(client, cmp, d_id, "5"))
    assert answer(d, t["id"]).status_code == 200
    # The sender alone holds 60% (PROPOSAL: the sender's share counts).
    assert shares(cmp)[d_id] == Decimal(5)


def test_whole_share_of_a_co_owner_ends_his_position(a: tuple[Client, str], cmp: str) -> None:
    client, me = a
    b, b_id = _two_owners(client, cmp)
    t = started(transfer(b, cmp, me, "30"))
    assert answer(client, t["id"]).status_code == 200
    assert shares(cmp) == {me: Decimal(100)}
    # § 3.4.4: the dedicated position ends with its access.
    assert b.get(f"/api/c/{cmp}").status_code == 404
    assert Employment.objects.get(company__public_id=cmp, user__public_id=b_id).state == (
        "terminated"
    )


def test_an_existing_employee_keeps_his_employment_as_owner(
    a: tuple[Client, str], cmp: str
) -> None:
    client, me = a
    worker, employment = hire(
        client, cmp, "w@example.com", [{"type": "grant", "permission": "client.list"}]
    )
    w_id = worker.get("/api/me").json()["user"]["public_id"]
    t = started(transfer(client, cmp, w_id, "10"))
    assert answer(worker, t["id"]).status_code == 200
    # The same employment holds the owner's position; his rights stay (§ 3.4.4).
    assert worker.get(f"/api/c/{cmp}").json()["permissions"] == ["client.list"]
    t = started(transfer(worker, cmp, me, "10"))
    assert answer(client, t["id"]).status_code == 200
    assert Employment.objects.get(pk=employment).state == "active"


# --- Succession of the top authority (§ 3.4.6) -------------------------------------------------


def test_top_authority_needs_a_successor_for_the_whole_share(
    a: tuple[Client, str], cmp: str
) -> None:
    client, me = a
    b, b_id = user("b@example.com")
    _x, x_id = user("x@example.com")
    assert code(transfer(client, cmp, b_id, "100")) == "TOP_AUTHORITY_SUCCESSOR_REQUIRED"
    assert code(transfer(client, cmp, b_id, "100", successor=x_id)) == "SUCCESSOR_NOT_OWNER"
    assert code(transfer(client, cmp, b_id, "50", successor=b_id)) == "SUCCESSOR_NOT_EXPECTED"
    t = started(transfer(client, cmp, b_id, "100", successor=b_id))
    assert answer(b, t["id"], role="recipient").status_code == 200
    # Until the successor accepts, the director keeps the share and the authority.
    assert shares(cmp) == {me: Decimal(100)}
    assert client.get(f"/api/c/{cmp}").json()["company"]["employment"]["is_top_authority"]
    assert answer(b, t["id"], role="successor").status_code == 200
    assert shares(cmp) == {b_id: Decimal(100)}
    assert b.get(f"/api/c/{cmp}").json()["company"]["employment"]["is_top_authority"]
    # The former director loses the former full access.
    assert client.get(f"/api/c/{cmp}").status_code == 404


def test_successor_may_be_another_owner(a: tuple[Client, str], cmp: str) -> None:
    client, me = a
    b, b_id = _two_owners(client, cmp)
    c, c_id = user("c@example.com")
    t = started(transfer(client, cmp, c_id, "70", successor=b_id))
    assert answer(c, t["id"]).status_code == 200
    assert shares(cmp)[me] == Decimal(70)
    assert answer(b, t["id"], role="successor").status_code == 200
    assert shares(cmp) == {b_id: Decimal(30), c_id: Decimal(70)}
    assert b.get(f"/api/c/{cmp}").json()["company"]["employment"]["is_top_authority"]
    assert not c.get(f"/api/c/{cmp}").json()["company"]["employment"]["is_top_authority"]


# --- Corporate ownership (§ 3.4.1) ---------------------------------------------------------------


def _corporate(client: Client, cmp: str) -> tuple[str, Client, str, Client, str]:
    """Company O owned by X (60%) and Y (40%) receives 20% of `cmp`."""
    x, x_id = user("x@example.com")
    o = company(x, "Holding")
    y, y_id = user("y@example.com")
    t = started(transfer(x, o, y_id, "40"))
    assert answer(y, t["id"]).status_code == 200
    t = started(transfer(client, cmp, o, "20"))
    assert answer(x, t["id"]).status_code == 200
    assert OwnershipShare.objects.filter(owner_company__public_id=o).count() == 0
    # Each owner of the receiving company confirms; one answer is not enough.
    assert answer(y, t["id"]).status_code == 200
    return o, x, x_id, y, y_id


def test_company_receives_a_share_after_each_owner_confirms(
    a: tuple[Client, str], cmp: str
) -> None:
    client, me = a
    o, x, _x_id, _y, _y_id = _corporate(client, cmp)
    assert shares(cmp) == {me: Decimal(80), o: Decimal(20)}
    owners = client.get(f"/api/c/{cmp}/owners").json()["owners"]
    assert {"type": "company", "public_id": o, "display_name": "Holding"} in [
        o_["owner"] for o_ in owners
    ]
    # The representative position stays vacant; nobody of O works in the company.
    held = x.get(f"/api/c/{o}/holdings").json()["holdings"]
    assert held[0]["seat"]["holder"] is None and held[0]["percent"] == "20.0000"
    assert x.get(f"/api/c/{cmp}").status_code == 404


def test_representative_rights_stay_within_the_limit(a: tuple[Client, str], cmp: str) -> None:
    client, _ = a
    o, x, _x_id, _y, _y_id = _corporate(client, cmp)
    seat = x.get(f"/api/c/{o}/holdings").json()["holdings"][0]["seat"]["id"]
    limit = [{"type": "grant", "permission": "client.list"}]
    put = client.put(
        f"/api/c/{cmp}/owners/seats/{seat}/limit",
        data={"rights": limit},
        content_type="application/json",
        headers=key(),
    )
    assert put.status_code == 200, put.content
    r, r_id = user("r@example.com")
    wide = {"target": r_id, "rights": [{"type": "grant", "permission": "client.create"}]}
    refused = post(x, f"/api/c/{o}/holdings/{seat}/representative", wide, key())
    assert code(refused) == "REPRESENTATIVE_RIGHTS_EXCEED_LIMIT"
    ok = post(
        x, f"/api/c/{o}/holdings/{seat}/representative", {"target": r_id, "rights": limit}, key()
    )
    assert ok.status_code == 200, ok.content
    invitation = r.get("/api/invitations").json()["invitations"][0]
    assert post(r, f"/api/invitations/{invitation['id']}/accept", {}, key()).status_code == 200
    assert r.get(f"/api/c/{cmp}").json()["permissions"] == ["client.list"]
    # The receiving company sees them apart from the ordinary rights its editor changes.
    card = next(
        e
        for e in client.get(f"/api/c/{cmp}/employees").json()["employees"]
        if e["user"] and e["user"]["public_id"] == r_id
    )
    assert card["rights"] == [] and len(card["owner_seat_rights"]) == 1
    assert card["position"]["code"] == "owner_representative"
    # A narrowed limit cuts the representative's rights (PROPOSAL).
    client.put(
        f"/api/c/{cmp}/owners/seats/{seat}/limit",
        data={"rights": []},
        content_type="application/json",
        headers=key(),
    )
    assert r.get(f"/api/c/{cmp}").json()["permissions"] == []
    # Only a person with full rights of the owner company appoints (§ 3.4.1).
    assert (
        post(r, f"/api/c/{o}/holdings/{seat}/representative", ok.json(), key()).status_code == 404
    )
    removed = post(x, f"/api/c/{o}/holdings/{seat}/representative/remove", {}, key())
    assert removed.status_code == 200
    assert r.get(f"/api/c/{cmp}").status_code == 404


def test_corporate_share_disposal_needs_each_owner(a: tuple[Client, str], cmp: str) -> None:
    client, me = a
    o, x, _x_id, y, _y_id = _corporate(client, cmp)
    seat = x.get(f"/api/c/{o}/holdings").json()["holdings"][0]["seat"]["id"]
    t = started(transfer(x, cmp, me, "5", path=f"/api/c/{o}/holdings/{seat}/transfers"))
    assert answer(client, t["id"], role="recipient").status_code == 200
    assert answer(x, t["id"], role="sender_owner").status_code == 200
    assert shares(cmp)[o] == Decimal(20)
    assert answer(y, t["id"], role="sender_owner").status_code == 200
    assert shares(cmp) == {me: Decimal(85), o: Decimal(15)}


def test_company_successor_needs_a_filled_representative(a: tuple[Client, str], cmp: str) -> None:
    client, me = a
    o, x, _x_id, y, _y_id = _corporate(client, cmp)
    t = started(transfer(client, cmp, o, "80", successor=o))
    for person in (x, y):
        answer(person, t["id"], role="recipient_owner")
    refused = answer(x, t["id"], role="successor")
    assert code(refused) == "SUCCESSOR_REPRESENTATIVE_REQUIRED"
    assert shares(cmp)[me] == Decimal(80)


# --- Cycles (§ 3.5.4) ---------------------------------------------------------------------------


def test_no_own_share_and_no_cycle(a: tuple[Client, str], cmp: str) -> None:
    client, me = a
    assert code(transfer(client, cmp, cmp, "10")) == "OWNERSHIP_CYCLE"
    other = company(client, "Second")
    t = started(transfer(client, cmp, other, "10"))
    answer(client, t["id"])
    assert shares(cmp)[other] == Decimal(10)
    # `other` owns `cmp`; `cmp` may not own `other` (A → B → A).
    assert code(transfer(client, other, cmp, "10")) == "OWNERSHIP_CYCLE"
    third = company(client, "Third")
    t = started(transfer(client, other, third, "10"))
    answer(client, t["id"])
    # third → other → cmp: cmp may not own third.
    assert code(transfer(client, third, cmp, "10")) == "OWNERSHIP_CYCLE"
    assert shares(third) == {me: Decimal(100)}


def test_cycle_is_checked_again_at_acceptance(a: tuple[Client, str], cmp: str) -> None:
    client, _ = a
    other = company(client, "Second")
    first = started(transfer(client, cmp, other, "10"))
    second = started(transfer(client, other, cmp, "10"))
    answer(client, first["id"])
    assert code(answer(client, second["id"])) == "OWNERSHIP_CYCLE"
