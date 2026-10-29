"""Rights, invitations, blocking (ТЗ § 2.5.1–2.5.2, § 5.1, § 23.1–23.3)."""

from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from django.test import Client
from django.utils import timezone

from c4rsys.companies.access import RightsResolver
from c4rsys.companies.models import (
    Employment,
    Invitation,
    PermissionAssignment,
    PermissionSet,
    Position,
)
from c4rsys.companies.permissions import PERMISSIONS
from c4rsys.platform.commands import Actor
from c4rsys.platform.models import AuditEvent
from tests.accounts.helpers import post, register
from tests.companies.helpers import create_company, named_user

pytestmark = pytest.mark.django_db

_keys = iter(range(10**6))


def _key() -> dict[str, str]:
    return {"Idempotency-Key": f"k-{next(_keys)}"}


@pytest.fixture
def cmp(owner: Client) -> str:
    public_id: str = create_company(owner, timezone="Asia/Bangkok").json()["company"]["public_id"]
    return public_id


def _user(email: str) -> tuple[Client, str]:
    client = Client()
    named_user(client, email)
    return client, client.get("/api/me").json()["user"]["public_id"]


def _location(client: Client, cmp: str, name: str) -> str:
    body = post(client, f"/api/c/{cmp}/locations", {"name": name}, headers=_key()).json()
    location_id: str = body["location"]["id"]
    return location_id


def _invite(
    client: Client,
    cmp: str,
    target: str,
    rights: list[dict[str, Any]] | None = None,
    position_name: str = "Manager",
    **extra: Any,
) -> Any:
    body = {"target": target, "position_name": position_name, "rights": rights or [], **extra}
    return post(client, f"/api/c/{cmp}/employees/invitations", body, headers=_key())


def _accept(client: Client, invitation_id: str, **flags: bool) -> Any:
    return post(client, f"/api/invitations/{invitation_id}/accept", flags, headers=_key())


def _hire(owner: Client, cmp: str, email: str, rights: list[dict[str, Any]]) -> tuple[Client, str]:
    """Invites and accepts; returns the employee's client and employment ID."""
    client, public_id = _user(email)
    invitation = _invite(owner, cmp, public_id, rights).json()["invitation"]
    assert _accept(client, invitation["id"]).status_code == 200
    return client, invitation["employment_id"]


def _rights(owner: Client, cmp: str, employment_id: str, rights: list[dict[str, Any]]) -> Any:
    version = owner.get(f"/api/c/{cmp}/employees/{employment_id}").json()["employee"]["version"]
    return owner.put(
        f"/api/c/{cmp}/employees/{employment_id}/rights",
        data={"rights": rights, "expected_version": version},
        content_type="application/json",
        headers=_key(),
    )


def _actor(employment_id: str) -> Actor:
    employment = Employment.objects.get(pk=employment_id)
    assert employment.user_id is not None
    return Actor(
        user_id=employment.user_id, company_id=employment.company_id, employment_id=employment.id
    )


SET_RENTAL = {"type": "set", "set": "rental_manager"}
SET_ADMIN = {"type": "set", "set": "company_admin"}


def test_system_sets_of_stage_one() -> None:
    # § 23.1.14, § 23.2: system sets so far; Driver has no permission of the stage.
    sets = {s.code: s for s in PermissionSet.objects.filter(is_system=True)}
    assert set(sets) == {
        "company_admin",
        "rental_manager",
        "driver",
        "accountant",
        "storekeeper",
        "service_manager",
        "mechanic",
    }
    admin = set(sets["company_admin"].permissions.values_list("code", flat=True))
    assert admin == set(PERMISSIONS)
    assert not sets["driver"].permissions.exists()
    assert "order.close_with_debt" not in set(
        sets["rental_manager"].permissions.values_list("code", flat=True)
    )


def test_invite_by_id_and_accept(owner: Client, cmp: str) -> None:
    # § 5.1.1, § 5.1.3, § 5.1.7.
    client, public_id = _user("worker@example.com")
    response = _invite(owner, cmp, public_id, [SET_RENTAL])
    assert response.status_code == 201, response.content
    invitation = response.json()["invitation"]
    card = Employment.objects.get(pk=invitation["employment_id"])
    assert card.state == Employment.State.INVITED and card.user_id is None

    mine = client.get("/api/invitations").json()["invitations"]
    assert [i["id"] for i in mine] == [invitation["id"]]
    assert mine[0]["company"]["public_id"] == cmp
    assert mine[0]["data_given"]["required"] == ["first_name", "last_name"]

    accepted = _accept(client, invitation["id"], share_middle_name=False, share_email=True)

    assert accepted.status_code == 200, accepted.content
    card.refresh_from_db()
    assert card.state == Employment.State.ACTIVE and card.user is not None
    assert card.user.public_id == public_id
    employee = owner.get(f"/api/c/{cmp}/employees/{card.id}").json()["employee"]
    assert employee["user"]["email"] == "worker@example.com"
    assert employee["user"]["middle_name"] is None
    assert [r["set"] for r in employee["rights"]] == ["rental_manager"]
    assert client.get(f"/api/c/{cmp}").status_code == 200


def test_invitation_by_email_reaches_user_registered_later(owner: Client, cmp: str) -> None:
    # § 5.1.3: no new invitation is needed after registration.
    invitation = _invite(owner, cmp, "Newcomer@Example.com").json()["invitation"]

    client = Client()
    named_user(client, "newcomer@example.com")
    mine = client.get("/api/invitations").json()["invitations"]

    assert [i["id"] for i in mine] == [invitation["id"]]
    assert _accept(client, invitation["id"]).status_code == 200


def test_accept_requires_full_name(owner: Client, cmp: str) -> None:
    # § 2.1.3.
    invitation = _invite(owner, cmp, "noname@example.com").json()["invitation"]
    client = Client()
    register(client, "noname@example.com")

    response = _accept(client, invitation["id"])

    assert response.json()["error"]["code"] == "NAME_REQUIRED"


def test_strangers_cannot_see_or_accept_invitation(owner: Client, cmp: str) -> None:
    invitation = _invite(owner, cmp, "worker@example.com").json()["invitation"]
    stranger, _ = _user("stranger@example.com")

    assert stranger.get("/api/invitations").json()["invitations"] == []
    assert _accept(stranger, invitation["id"]).status_code == 404


def test_repeat_with_same_conditions_is_a_reminder(owner: Client, cmp: str) -> None:
    # § 2.5.2: no copy; the term may be extended.
    first = _invite(owner, cmp, "worker@example.com", [SET_RENTAL]).json()["invitation"]
    Invitation.objects.update(expires_at=timezone.now() + timedelta(days=1))

    second = _invite(owner, cmp, "worker@example.com", [SET_RENTAL]).json()["invitation"]

    assert second["id"] == first["id"]
    assert Invitation.objects.count() == 1
    assert Invitation.objects.get().expires_at > timezone.now() + timedelta(days=6)


def test_changed_conditions_replace_pending_invitation_on_same_card(
    owner: Client, cmp: str
) -> None:
    # § 2.5.2.
    first = _invite(owner, cmp, "worker@example.com", [SET_RENTAL]).json()["invitation"]

    second = _invite(owner, cmp, "worker@example.com", [{"type": "set", "set": "accountant"}])

    new = second.json()["invitation"]
    assert new["id"] != first["id"]
    assert new["employment_id"] == first["employment_id"]
    assert Invitation.objects.get(pk=first["id"]).state == Invitation.State.CANCELLED


def test_expired_invitation_cannot_be_accepted(owner: Client, cmp: str) -> None:
    # § 2.5.1: 7 days, then a new invitation is needed.
    client, public_id = _user("worker@example.com")
    invitation = _invite(owner, cmp, public_id).json()["invitation"]
    created = Invitation.objects.get()
    assert created.expires_at - created.created_at == timedelta(days=7)
    Invitation.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

    assert _accept(client, invitation["id"]).json()["error"]["code"] == "INVITATION_EXPIRED"
    assert client.get("/api/invitations").json()["invitations"] == []

    renewed = _invite(owner, cmp, public_id).json()["invitation"]
    assert renewed["employment_id"] == invitation["employment_id"]
    assert _accept(client, renewed["id"]).status_code == 200


def test_cancel_and_decline(owner: Client, cmp: str) -> None:
    client, public_id = _user("worker@example.com")
    first = _invite(owner, cmp, public_id).json()["invitation"]
    cancel = post(owner, f"/api/c/{cmp}/employees/invitations/{first['id']}/cancel", headers=_key())
    assert cancel.json()["invitation"]["state"] == "cancelled"
    assert _accept(client, first["id"]).json()["error"]["code"] == "INVITATION_NOT_PENDING"

    second = _invite(owner, cmp, public_id).json()["invitation"]
    declined = post(client, f"/api/invitations/{second['id']}/decline", headers=_key())

    assert declined.json()["invitation"]["state"] == "declined"
    # The card stays (§ 5.1.1).
    assert Employment.objects.filter(pk=second["employment_id"]).exists()


def test_current_employee_cannot_be_invited_again(owner: Client, cmp: str) -> None:
    _client, employment_id = _hire(owner, cmp, "worker@example.com", [])
    public_id = Employment.objects.get(pk=employment_id).user.public_id  # type: ignore[union-attr]

    response = _invite(owner, cmp, public_id)

    assert response.json()["error"]["code"] == "EMPLOYEE_ALREADY_EXISTS"


def test_position_is_added_to_handbook_and_director_is_not_assignable(
    owner: Client, cmp: str
) -> None:
    # § 5.1.5; the top-authority position is not given by invitation.
    _invite(owner, cmp, "a@example.com", position_name="Manager")
    _invite(owner, cmp, "b@example.com", position_name="manager")
    assert Position.objects.filter(name__iexact="manager").count() == 1

    director = Position.objects.get(code="director")
    response = _invite(owner, cmp, "c@example.com", position_name="", position_id=str(director.id))
    assert response.json()["error"]["code"] == "POSITION_NOT_ASSIGNABLE"


def test_employee_without_permission_is_refused(owner: Client, cmp: str) -> None:
    # Stage 1 done-criterion: an employee without the right gets a refusal.
    driver, _ = _hire(owner, cmp, "driver@example.com", [{"type": "set", "set": "driver"}])

    assert driver.get(f"/api/c/{cmp}").status_code == 200
    assert driver.get(f"/api/c/{cmp}/locations").status_code == 200
    for response in (
        driver.patch(
            f"/api/c/{cmp}",
            data={"timezone": "UTC", "expected_version": 1},
            content_type="application/json",
            headers=_key(),
        ),
        post(driver, f"/api/c/{cmp}/locations", {"name": "X"}, headers=_key()),
        _invite(driver, cmp, "x@example.com"),
        driver.get(f"/api/c/{cmp}/employees"),
    ):
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "PERMISSION_DENIED"


def test_user_of_another_company_does_not_see_its_employees(owner: Client, cmp: str) -> None:
    # Stage 1 done-criterion: a user of another company does not see the object.
    _client, employment_id = _hire(owner, cmp, "worker@example.com", [])
    other, _ = _user("other@example.com")
    other_cmp = create_company(other).json()["company"]["public_id"]

    assert other.get(f"/api/c/{cmp}/employees/{employment_id}").status_code == 404
    assert other.get(f"/api/c/{other_cmp}/employees/{employment_id}").status_code == 404


def test_scope_by_location_and_personal_overrides(owner: Client, cmp: str) -> None:
    # § 23.1.1, § 23.1.2, § 23.1.9, § 23.1.13.
    phuket = _location(owner, cmp, "Phuket")
    krabi = _location(owner, cmp, "Krabi")
    _client, employment_id = _hire(
        owner, cmp, "worker@example.com", [{**SET_RENTAL, "locations": [phuket]}]
    )
    resolver, actor = RightsResolver(), _actor(employment_id)

    assert resolver.has_permission(actor, "rental.issue", location_id=UUID(phuket))
    assert not resolver.has_permission(actor, "rental.issue", location_id=UUID(krabi))
    assert not resolver.has_permission(actor, "order.close_with_debt", location_id=UUID(phuket))

    response = _rights(
        owner,
        cmp,
        employment_id,
        [
            {**SET_RENTAL, "locations": [phuket]},
            {"type": "grant", "permission": "order.close_with_debt", "locations": [phuket]},
            {"type": "deny", "permission": "rental.issue", "locations": [phuket]},
        ],
    )
    assert response.status_code == 200, response.content

    assert resolver.has_permission(actor, "order.close_with_debt", location_id=UUID(phuket))
    assert not resolver.has_permission(actor, "rental.issue", location_id=UUID(phuket))
    assert resolver.has_permission(actor, "rental.accept_return", location_id=UUID(phuket))


def test_union_of_sets_and_context_scope(owner: Client, cmp: str) -> None:
    # § 23.1.10, § 23.1.13: one permission from two sets is not lost when one set is removed.
    phuket = _location(owner, cmp, "Phuket")
    context = post(
        owner,
        f"/api/c/{cmp}/locations/{phuket}/contexts",
        {"type": "rental"},
        headers=_key(),
    ).json()["location"]["contexts"][0]["id"]
    _client, employment_id = _hire(
        owner, cmp, "worker@example.com", [SET_RENTAL, {"type": "set", "set": "accountant"}]
    )
    resolver, actor = RightsResolver(), _actor(employment_id)

    _rights(owner, cmp, employment_id, [{"type": "set", "set": "accountant"}])
    assert resolver.has_permission(actor, "order.view")
    assert not resolver.has_permission(actor, "rental.issue")

    _rights(
        owner,
        cmp,
        employment_id,
        [{**SET_RENTAL, "locations": [phuket], "contexts": [context]}],
    )
    assert resolver.has_permission(
        actor, "rental.issue", location_id=UUID(phuket), context_id=UUID(context)
    )
    revoked = PermissionAssignment.objects.filter(revoked_at__isnull=False)
    assert revoked.count() == 2
    assert all(a.revoked_by_user_id is not None for a in revoked)


def test_rights_change_keeps_unchanged_assignments(owner: Client, cmp: str) -> None:
    _client, employment_id = _hire(owner, cmp, "worker@example.com", [SET_RENTAL])
    kept = PermissionAssignment.objects.get()

    _rights(owner, cmp, employment_id, [SET_RENTAL, {"type": "set", "set": "accountant"}])

    kept.refresh_from_db()
    assert kept.revoked_at is None
    assert PermissionAssignment.objects.filter(revoked_at__isnull=True).count() == 2
    assert AuditEvent.objects.filter(action="employee.rights_changed").count() == 1


def test_admin_manages_rights_but_not_broad_ones_nor_own(owner: Client, cmp: str) -> None:
    # § 23.3.2, § 23.3.5 with the stage 1 decision: broad rights only by the director.
    admin, admin_employment = _hire(owner, cmp, "admin@example.com", [SET_ADMIN])
    _client, worker = _hire(owner, cmp, "worker@example.com", [])

    assert _rights(admin, cmp, worker, [SET_RENTAL]).status_code == 200
    broad = _rights(admin, cmp, worker, [SET_ADMIN])
    assert broad.json()["error"]["code"] == "BROAD_RIGHTS_REQUIRE_TOP_AUTHORITY"
    grant = _rights(admin, cmp, worker, [{"type": "grant", "permission": "employee.rights.manage"}])
    assert grant.json()["error"]["code"] == "BROAD_RIGHTS_REQUIRE_TOP_AUTHORITY"
    own = _rights(admin, cmp, admin_employment, [])
    assert own.json()["error"]["code"] == "SELF_RIGHTS_CHANGE_FORBIDDEN"
    assert _invite(admin, cmp, "third@example.com", [SET_ADMIN]).status_code == 422


def test_offering_rights_needs_rights_management(owner: Client, cmp: str) -> None:
    # § 23.3.1: inviting and changing rights are separate permissions.
    inviter, _ = _hire(
        owner, cmp, "hr@example.com", [{"type": "grant", "permission": "employee.invite"}]
    )

    assert _invite(inviter, cmp, "a@example.com").status_code == 201
    assert _invite(inviter, cmp, "b@example.com", [SET_RENTAL]).status_code == 403


def test_block_and_restore(owner: Client, cmp: str) -> None:
    # § 23.3.8, § 23.3.10.
    worker, employment_id = _hire(owner, cmp, "worker@example.com", [SET_RENTAL])
    blocker, blocker_id = _hire(
        owner, cmp, "hr@example.com", [{"type": "grant", "permission": "employee.access.block"}]
    )

    blocked = post(blocker, f"/api/c/{cmp}/employees/{employment_id}/block", headers=_key())
    assert blocked.json()["employee"]["state"] == "blocked"
    denied = worker.get(f"/api/c/{cmp}")
    assert (denied.status_code, denied.json()["error"]["code"]) == (403, "EMPLOYMENT_BLOCKED")
    assert not RightsResolver().has_permission(_actor(employment_id), "rental.issue")

    # Blocking does not give the right to restore.
    restore = post(blocker, f"/api/c/{cmp}/employees/{employment_id}/restore", headers=_key())
    assert restore.status_code == 403
    restored = post(owner, f"/api/c/{cmp}/employees/{employment_id}/restore", headers=_key())
    assert restored.json()["employee"]["state"] == "active"
    assert worker.get(f"/api/c/{cmp}").status_code == 200

    own = post(blocker, f"/api/c/{cmp}/employees/{blocker_id}/block", headers=_key())
    assert own.json()["error"]["code"] == "SELF_ACCESS_CHANGE_FORBIDDEN"
    director = Employment.objects.get(position__is_top_authority=True)
    top = post(blocker, f"/api/c/{cmp}/employees/{director.id}/block", headers=_key())
    assert top.json()["error"]["code"] == "EMPLOYEE_IS_TOP_AUTHORITY"


def test_invitation_flow_is_journaled(owner: Client, cmp: str) -> None:
    _hire(owner, cmp, "worker@example.com", [SET_RENTAL])

    actions = set(AuditEvent.objects.values_list("action", flat=True))

    assert {"employment.created", "invitation.sent", "invitation.accepted"} <= actions
    accepted = AuditEvent.objects.get(action="invitation.accepted")
    assert accepted.company_id == Employment.objects.get(position__code="").company_id


def test_company_answer_lists_own_permissions(owner: Client, cmp: str) -> None:
    # The interface hides actions without the right; the server still checks them.
    driver, _ = _hire(owner, cmp, "driver@example.com", [{"type": "set", "set": "driver"}])
    accountant, _ = _hire(owner, cmp, "acc@example.com", [{"type": "set", "set": "accountant"}])

    assert set(owner.get(f"/api/c/{cmp}").json()["permissions"]) == set(PERMISSIONS)
    assert driver.get(f"/api/c/{cmp}").json()["permissions"] == []
    assert accountant.get(f"/api/c/{cmp}").json()["permissions"] == [
        "client.find",
        "finance.account.view",
        "finance.article.manage",
        "finance.correct",
        "finance.deposit.manage",
        "finance.income.record",
        "finance.offset",
        "finance.payment.receive",
        "finance.payout",
        "finance.settlement.manage",
        "finance.transfer",
        "order.view",
        "rental.view",
        "warehouse.price.set",
    ]


def test_permission_catalog_for_the_rights_editor(owner: Client, cmp: str) -> None:
    body = owner.get(f"/api/c/{cmp}/permission-catalog").json()

    assert [s["code"] for s in body["sets"]] == [
        "company_admin",
        "rental_manager",
        "driver",
        "accountant",
        "storekeeper",
        "service_manager",
        "mechanic",
    ]
    assert "order.close_with_debt" in body["permissions"]
