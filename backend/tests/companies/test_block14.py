"""Block 14: own permission sets, applying an updated set, the confirmation of especially broad
rights and the separate administrative rights (ТЗ § 23.1.3, § 23.1.5, § 23.1.15, § 23.3.5,
§ 23.3.7, § 23.3.9, § 23.5.1, § 23.5.3, § 23.8.2, § 23.10.2)."""

import importlib
from collections.abc import Iterator
from datetime import timedelta
from typing import Any
from unittest import mock

import pytest
from django.apps import apps
from django.test import Client
from django.utils import timezone

from c4rsys.companies.access import RightsResolver
from c4rsys.companies.models import (
    Employment,
    PermissionAssignment,
    PermissionSet,
    PermissionSetItem,
)
from c4rsys.companies.permissions import PERMISSIONS, SPLITS, SYSTEM_SETS
from c4rsys.notifications.models import Notification, NotificationRecipient
from c4rsys.platform.commands import Actor
from c4rsys.platform.models import AuditEvent
from c4rsys.tasks.models import Task
from tests.accounts.helpers import CODE, post
from tests.companies.helpers import broad_code, create_company, hire, key, named_user

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def many_codes() -> Iterator[None]:
    """Several one-time codes of one user within a minute in these tests."""
    with (
        mock.patch("c4rsys.accounts.otp.RESEND_INTERVAL", timedelta(0)),
        mock.patch("c4rsys.accounts.otp.HOUR_LIMIT", 100),
        mock.patch("c4rsys.accounts.otp.DAY_LIMIT", 100),
    ):
        yield


ADMIN = {"type": "set", "set": "company_admin"}
RENTAL = {"type": "set", "set": "rental_manager"}


def grant(code: str) -> dict[str, str]:
    return {"type": "grant", "permission": code}


@pytest.fixture
def cmp(owner: Client) -> str:
    public_id: str = create_company(owner, timezone="Asia/Bangkok").json()["company"]["public_id"]
    return public_id


def _actor(employment_id: str) -> Actor:
    e = Employment.objects.get(pk=employment_id)
    assert e.user_id is not None
    return Actor(user_id=e.user_id, company_id=e.company_id, employment_id=e.id)


def _can(employment_id: str, code: str) -> bool:
    return RightsResolver().has_permission(_actor(employment_id), code)


def _employee(client: Client, cmp: str, employment_id: str) -> dict[str, Any]:
    employee: dict[str, Any] = client.get(f"/api/c/{cmp}/employees/{employment_id}").json()[
        "employee"
    ]
    return employee


def _rights(
    client: Client, cmp: str, employment_id: str, rights: list[dict[str, Any]], **extra: Any
) -> Any:
    version = _employee(client, cmp, employment_id)["version"]
    return client.put(
        f"/api/c/{cmp}/employees/{employment_id}/rights",
        data={"rights": rights, "expected_version": version, **extra},
        content_type="application/json",
        headers=key(),
    )


def _new_set(client: Client, cmp: str, **body: Any) -> Any:
    return post(client, f"/api/c/{cmp}/permission-sets", body, key())


# --- Catalog ---------------------------------------------------------------------------------


def test_new_rights_are_in_the_catalog_and_the_sets() -> None:
    admin = set(
        PermissionSetItem.objects.filter(permission_set__code="company_admin").values_list(
            "permission_id", flat=True
        )
    )
    assert admin == set(PERMISSIONS)
    for broad, narrow in SPLITS.items():
        for code, members in SYSTEM_SETS.items():
            if broad in members:
                assert set(narrow) <= set(members), (code, broad)
        stored = PermissionSet.objects.filter(
            is_system=True,
            pk__in=PermissionSetItem.objects.filter(permission_id=broad).values("permission_set"),
        )
        for s in stored:
            codes = set(
                PermissionSetItem.objects.filter(permission_set=s).values_list(
                    "permission_id", flat=True
                )
            )
            assert set(narrow) <= codes, (s.code, broad)
    assert "employee.sets.manage" not in SYSTEM_SETS["rental_manager"]


def test_split_migration_keeps_the_access_of_existing_holders(owner: Client, cmp: str) -> None:
    """The data migration adds the narrower codes to every assignment of the broader one."""
    _c, employment = hire(owner, cmp, "w@example.com", [grant("company.settings"), RENTAL])
    copy = PermissionAssignment.objects.get(employment_id=employment, kind="set")
    copy.permissions = [c for c in copy.permissions if c not in SPLITS["client.edit"]]
    copy.save(update_fields=["permissions"])
    single = PermissionAssignment.objects.get(employment_id=employment, kind="grant")
    assert not _can(employment, "location.create")
    assert not _can(employment, "client.link.request")

    migration = importlib.import_module("c4rsys.companies.migrations.0037_block14_permissions")
    migration.forward(apps, None)

    assert _can(employment, "location.create")
    assert _can(employment, "warehouse.policy.manage")
    assert _can(employment, "client.link.request")
    single.refresh_from_db()
    assert single.permissions == ["company.settings"]  # the grant keeps one code
    siblings = PermissionAssignment.objects.filter(
        employment_id=employment, kind="grant", revoked_at__isnull=True
    ).exclude(pk=single.pk)
    assert {x.permissions[0] for x in siblings} == set(SPLITS["company.settings"])
    assert (
        AuditEvent.objects.filter(
            action="employee.rights_split", system_process="permission_split"
        ).count()
        == 2
    )
    migration.forward(apps, None)  # a repeat adds nothing
    assert AuditEvent.objects.filter(action="employee.rights_split").count() == 2


# --- Own sets (§ 23.1.5, § 23.3.9) -------------------------------------------------------------


def test_own_sets_are_managed_by_their_right(owner: Client, cmp: str) -> None:
    empty = _new_set(owner, cmp, name="Night desk")
    assert empty.status_code == 201, empty.content
    assert empty.json()["set"]["permissions"] == []
    based = _new_set(owner, cmp, name="Rental lite", based_on="rental_manager")
    assert set(based.json()["set"]["permissions"]) == set(SYSTEM_SETS["rental_manager"])
    assert based.json()["set"]["based_on"] == "rental_manager"
    taken = _new_set(owner, cmp, name="night DESK")
    assert taken.json()["error"]["code"] == "PERMISSION_SET_NAME_TAKEN"
    unknown = _new_set(owner, cmp, name="X", permissions=["no.such"])
    assert unknown.json()["error"]["code"] == "PERMISSION_UNKNOWN"

    hr, _ = hire(owner, cmp, "hr@example.com", [grant("employee.invite")])
    assert _new_set(hr, cmp, name="Mine").status_code == 403
    sets_manager, _ = hire(owner, cmp, "sm@example.com", [grant("employee.sets.manage")])
    assert _new_set(sets_manager, cmp, name="Mine").status_code == 201
    catalog = sets_manager.get(f"/api/c/{cmp}/permission-catalog").json()
    assert [s["name"] for s in catalog["sets"] if not s["is_system"]] == [
        "Night desk",
        "Rental lite",
        "Mine",
    ]
    assert catalog["sections"]["location.create"] == "location"
    assert catalog["broad"] == ["employee.rights.manage"]

    other = Client()
    named_user(other, "other@example.com")
    other_cmp = create_company(other, key="c-2").json()["company"]["public_id"]
    own_id = empty.json()["set"]["code"]
    worker, worker_employment = hire(other, other_cmp, "ow@example.com", [])
    foreign = _rights(other, other_cmp, worker_employment, [{"type": "set", "set": own_id}])
    assert foreign.json()["error"]["code"] == "PERMISSION_SET_UNKNOWN"
    assert worker.get(f"/api/c/{cmp}/permission-catalog").status_code == 404


def test_changed_set_reaches_holders_only_when_applied(owner: Client, cmp: str) -> None:
    created = _new_set(owner, cmp, name="Desk", permissions=["client.find"]).json()["set"]
    ref = {"type": "set", "set": created["code"]}
    _c, first = hire(owner, cmp, "a@example.com", [ref])
    _c, second = hire(owner, cmp, "b@example.com", [ref])
    assert _can(first, "client.find") and not _can(first, "client.list")

    changed = owner.patch(
        f"/api/c/{cmp}/permission-sets/{created['id']}",
        data={"permissions": ["client.list"], "expected_version": created["version"]},
        content_type="application/json",
        headers=key(),
    )
    assert changed.status_code == 200, changed.content
    # § 23.1.3: nobody's access changed.
    assert _can(first, "client.find") and not _can(first, "client.list")
    shown = _employee(owner, cmp, first)["rights"][0]
    assert shown["set_update"] == {"added": ["client.list"], "removed": ["client.find"]}

    holders = owner.get(f"/api/c/{cmp}/permission-sets/{created['code']}/holders").json()
    assert {h["employment_id"] for h in holders["holders"]} == {first, second}
    assert all(h["added"] == ["client.list"] for h in holders["holders"])

    applied = post(
        owner,
        f"/api/c/{cmp}/permission-sets/{created['code']}/apply",
        {"employment_ids": [first]},
        key(),
    )
    assert applied.status_code == 200, applied.content
    assert _can(first, "client.list") and not _can(first, "client.find")
    assert _can(second, "client.find")  # not chosen
    old = PermissionAssignment.objects.filter(employment_id=first).exclude(revoked_at=None).get()
    assert old.permissions == ["client.find"]  # history is kept
    assert AuditEvent.objects.filter(action="employee.rights_set_applied").count() == 1
    again = post(
        owner,
        f"/api/c/{cmp}/permission-sets/{created['code']}/apply",
        {"employment_ids": [first]},
        key(),
    )
    assert again.json()["error"]["code"] == "PERMISSION_SET_UP_TO_DATE"


def test_system_set_extended_by_c4rsys_is_applied_separately(owner: Client, cmp: str) -> None:
    # § 23.1.15: a new right of a system set does not reach the holders by itself.
    _c, worker = hire(owner, cmp, "w@example.com", [RENTAL])
    rental = PermissionSet.objects.get(code="rental_manager", is_system=True)
    PermissionSetItem.objects.create(permission_set=rental, permission_id="tariff.edit")
    assert not _can(worker, "tariff.edit")
    assert _employee(owner, cmp, worker)["rights"][0]["set_update"]["added"] == ["tariff.edit"]
    rights_manager, _ = hire(owner, cmp, "rm@example.com", [grant("employee.rights.manage")])
    # An employee without the right to manage rights cannot apply.
    clerk, _ = hire(owner, cmp, "c@example.com", [grant("employee.invite")])
    path = f"/api/c/{cmp}/permission-sets/rental_manager"
    refused = post(clerk, f"{path}/apply", {"employment_ids": [worker]}, key())
    assert refused.status_code == 403
    assert clerk.get(f"{path}/holders").status_code == 403
    applied = post(rights_manager, f"{path}/apply", {"employment_ids": [worker]}, key())
    assert applied.status_code == 200, applied.content
    assert _can(worker, "tariff.edit")
    assert _employee(owner, cmp, worker)["rights"][0]["set_update"] is None


def test_own_rights_are_never_applied_by_oneself(owner: Client, cmp: str) -> None:
    # § 23.3.2: the updated set is applied to the actor's own rights by somebody else.
    admin, admin_employment = hire(owner, cmp, "adm@example.com", [ADMIN])
    PermissionAssignment.objects.filter(employment_id=admin_employment).update(
        permissions=[c for c in PERMISSIONS if c != "employee.sets.manage"]
    )
    path = f"/api/c/{cmp}/permission-sets/company_admin"
    own = post(admin, f"{path}/apply", {"employment_ids": [admin_employment]}, key())
    assert own.json()["error"]["code"] == "SELF_RIGHTS_CHANGE_FORBIDDEN"
    holders = admin.get(f"{path}/holders").json()["holders"]
    assert [h["own"] for h in holders] == [True]
    assert holders[0]["added"] == ["employee.sets.manage"]
    # Adding non-broad rights to a holder of the broad set needs no code or confirmation.
    by_director = post(owner, f"{path}/apply", {"employment_ids": [admin_employment]}, key())
    assert by_director.status_code == 200, by_director.content
    assert by_director.json()["rights_requests"] == []


def test_archived_set_is_not_assigned_anew(owner: Client, cmp: str) -> None:
    created = _new_set(owner, cmp, name="Old", permissions=["client.find"]).json()["set"]
    ref = {"type": "set", "set": created["code"]}
    _c, holder = hire(owner, cmp, "h@example.com", [ref])
    archived = post(
        owner,
        f"/api/c/{cmp}/permission-sets/{created['id']}/archive",
        {"expected_version": created["version"]},
        key(),
    )
    assert archived.status_code == 200, archived.content
    assert archived.json()["set"]["archived"] is True
    assert _can(holder, "client.find")  # the given assignment keeps working
    _c, other = hire(owner, cmp, "o@example.com", [])
    assert _rights(owner, cmp, other, [ref]).json()["error"]["code"] == "PERMISSION_SET_ARCHIVED"
    # The unchanged assignment of the holder may be saved again.
    assert _rights(owner, cmp, holder, [ref]).status_code == 200
    changed = owner.patch(
        f"/api/c/{cmp}/permission-sets/{created['id']}",
        data={"name": "New", "expected_version": archived.json()["set"]["version"]},
        content_type="application/json",
        headers=key(),
    )
    assert changed.json()["error"]["code"] == "PERMISSION_SET_ARCHIVED"
    restored = post(
        owner,
        f"/api/c/{cmp}/permission-sets/{created['id']}/restore",
        {"expected_version": archived.json()["set"]["version"]},
        key(),
    )
    assert restored.json()["set"]["archived"] is False


# --- Especially broad rights (§ 23.3.5, § 2.1.1) -----------------------------------------------


def test_director_grants_broad_rights_with_a_code(owner: Client, cmp: str) -> None:
    _c, worker = hire(owner, cmp, "w@example.com", [])
    asked = _rights(owner, cmp, worker, [ADMIN])
    assert asked.json()["error"]["code"] == "BROAD_RIGHTS_CODE_REQUIRED"
    assert not _can(worker, "location.edit")
    wrong = _rights(owner, cmp, worker, [ADMIN], **{**broad_code(owner), "code": "000000"})
    assert wrong.json()["error"]["code"] == "CODE_INVALID"
    done = _rights(owner, cmp, worker, [ADMIN], **broad_code(owner))
    assert done.status_code == 200, done.content
    assert done.json()["rights_request"] is None
    assert _can(worker, "location.edit")
    entry = AuditEvent.objects.filter(action="employee.rights_changed").latest("occurred_at")
    assert entry.after is not None and entry.after["code_confirmed"] is True
    # Ordinary rights need no code.
    _c, other = hire(owner, cmp, "o@example.com", [])
    assert _rights(owner, cmp, other, [RENTAL]).status_code == 200
    # An invitation with broad rights asks for the code too.
    newcomer = Client()
    named_user(newcomer, "n@example.com")
    target = newcomer.get("/api/me").json()["user"]["public_id"]
    body = {"target": target, "position_name": "Admin", "rights": [ADMIN]}
    invite = post(owner, f"/api/c/{cmp}/employees/invitations", body, key())
    assert invite.json()["error"]["code"] == "BROAD_RIGHTS_CODE_REQUIRED"


def _admin_and_worker(owner: Client, cmp: str) -> tuple[Client, str, str]:
    admin, admin_employment = hire(owner, cmp, "adm@example.com", [ADMIN])
    _c, worker = hire(owner, cmp, "w@example.com", [])
    return admin, admin_employment, worker


def test_employee_request_waits_for_the_director(owner: Client, cmp: str) -> None:
    admin, admin_employment, worker = _admin_and_worker(owner, cmp)
    saved = _rights(admin, cmp, worker, [RENTAL, ADMIN])
    assert saved.status_code == 200, saved.content
    request = saved.json()["rights_request"]
    assert request["state"] == "pending"
    # The ordinary part took effect at once; the broad part waits.
    assert _can(worker, "rental.book") and not _can(worker, "location.edit")
    shown = _employee(owner, cmp, worker)
    assert [r["id"] for r in shown["rights_requests"]] == [request["id"]]

    # A mandatory notification and a system task for the director (§ 20.3.4, § 19.1.9).
    top = Employment.objects.get(position__is_top_authority=True, company__public_id=cmp)
    notice = Notification.objects.get(event="employee.rights_confirmation")
    assert NotificationRecipient.objects.filter(notification=notice, employment=top).exists()
    task = Task.objects.get(system_event="employee.rights_confirmation")
    assert (task.assignee_id, task.object_type, str(task.object_id)) == (
        top.id,
        "employment",
        worker,
    )

    # The initiator cannot confirm; the director confirms with the one-time code.
    path = f"/api/c/{cmp}/rights-requests/{request['id']}"
    by_initiator = post(admin, f"{path}/confirm", {"flow": "x", "code": CODE}, key())
    assert by_initiator.json()["error"]["code"] == "RIGHTS_CONFIRMATION_REQUIRES_TOP_AUTHORITY"
    no_code = post(owner, f"{path}/confirm", {}, key())
    assert no_code.status_code == 400
    confirmed = post(owner, f"{path}/confirm", broad_code(owner), key())
    assert confirmed.status_code == 200, confirmed.content
    assert confirmed.json()["request"]["state"] == "confirmed"
    assert _can(worker, "location.edit")
    granted = PermissionAssignment.objects.get(
        employment_id=worker, permission_set__code="company_admin", revoked_at=None
    )
    assert granted.granted_by_user_id == Employment.objects.get(pk=admin_employment).user_id
    task.refresh_from_db()
    notice.refresh_from_db()
    assert (task.state, notice.state) == ("done", "done")
    assert post(owner, f"{path}/confirm", broad_code(owner), key()).json()["error"]["code"] == (
        "RIGHTS_REQUEST_NOT_PENDING"
    )


def test_request_is_declined_cancelled_or_outdated(owner: Client, cmp: str) -> None:
    admin, _admin_employment, worker = _admin_and_worker(owner, cmp)
    broad = [grant("employee.rights.manage")]

    first = _rights(admin, cmp, worker, broad).json()["rights_request"]
    declined = post(
        owner, f"/api/c/{cmp}/rights-requests/{first['id']}/decline", {"reason": "No"}, key()
    )
    assert declined.json()["request"]["state"] == "declined"
    assert not _can(worker, "employee.rights.manage")
    assert Task.objects.get(system_event="employee.rights_confirmation").state == "cancelled"

    second = _rights(admin, cmp, worker, broad).json()["rights_request"]
    by_owner = post(owner, f"/api/c/{cmp}/rights-requests/{second['id']}/cancel", {}, key())
    assert by_owner.status_code == 403
    cancelled = post(admin, f"/api/c/{cmp}/rights-requests/{second['id']}/cancel", {}, key())
    assert cancelled.json()["request"]["state"] == "cancelled"

    # Fresh data: an own set changed after the request makes it outdated.
    created = _new_set(owner, cmp, name="Rights", permissions=["employee.rights.manage"]).json()[
        "set"
    ]
    third = _rights(admin, cmp, worker, [{"type": "set", "set": created["code"]}]).json()[
        "rights_request"
    ]
    owner.patch(
        f"/api/c/{cmp}/permission-sets/{created['id']}",
        data={
            "permissions": ["employee.rights.manage", "client.find"],
            "expected_version": created["version"],
        },
        content_type="application/json",
        headers=key(),
    )
    outdated = post(
        owner, f"/api/c/{cmp}/rights-requests/{third['id']}/confirm", broad_code(owner), key()
    )
    assert outdated.json()["request"]["state"] == "outdated"
    assert outdated.json()["request"]["note"] == "set_changed"
    assert not _can(worker, "employee.rights.manage")
    listed = owner.get(f"/api/c/{cmp}/rights-requests?state=pending").json()["requests"]
    assert listed == []


def test_applying_a_set_that_becomes_broad_waits_for_the_director(owner: Client, cmp: str) -> None:
    created = _new_set(owner, cmp, name="Team lead", permissions=["client.find"]).json()["set"]
    _c, worker = hire(owner, cmp, "w@example.com", [{"type": "set", "set": created["code"]}])
    owner.patch(
        f"/api/c/{cmp}/permission-sets/{created['id']}",
        data={
            "permissions": ["client.find", "employee.rights.manage"],
            "expected_version": created["version"],
        },
        content_type="application/json",
        headers=key(),
    )
    admin, _ = hire(owner, cmp, "adm@example.com", [ADMIN])
    path = f"/api/c/{cmp}/permission-sets/{created['code']}"
    holders = admin.get(f"{path}/holders").json()["holders"]
    assert holders[0]["broad"] is True
    by_admin = post(admin, f"{path}/apply", {"employment_ids": [worker]}, key())
    assert by_admin.status_code == 200, by_admin.content
    request = by_admin.json()["rights_requests"][0]
    assert not _can(worker, "employee.rights.manage")
    confirmed = post(
        owner, f"/api/c/{cmp}/rights-requests/{request['id']}/confirm", broad_code(owner), key()
    )
    assert confirmed.json()["request"]["state"] == "confirmed", confirmed.content
    assert _can(worker, "employee.rights.manage")
    active = PermissionAssignment.objects.filter(employment_id=worker, revoked_at=None)
    assert [sorted(a.permissions) for a in active] == [["client.find", "employee.rights.manage"]]


# --- Separate administrative and section rights (§ 23.3.7, § 23.5.3) -------------------------


def test_locations_have_rights_of_their_own(owner: Client, cmp: str) -> None:
    settings, _ = hire(owner, cmp, "s@example.com", [grant("company.settings")])
    assert post(settings, f"/api/c/{cmp}/locations", {"name": "A"}, key()).status_code == 403
    creator, _ = hire(owner, cmp, "c@example.com", [grant("location.create")])
    created = post(creator, f"/api/c/{cmp}/locations", {"name": "A"}, key())
    assert created.status_code == 201, created.content
    location = created.json()["location"]
    patch = creator.patch(
        f"/api/c/{cmp}/locations/{location['id']}",
        data={"name": "B", "expected_version": location["version"]},
        content_type="application/json",
        headers=key(),
    )
    assert patch.status_code == 403
    contexts = post(
        creator, f"/api/c/{cmp}/locations/{location['id']}/contexts", {"type": "rental"}, key()
    )
    assert contexts.status_code == 403
    editor, _ = hire(
        owner, cmp, "e@example.com", [{**grant("location.edit"), "locations": [location["id"]]}]
    )
    patch = editor.patch(
        f"/api/c/{cmp}/locations/{location['id']}",
        data={"name": "B", "expected_version": location["version"]},
        content_type="application/json",
        headers=key(),
    )
    assert patch.status_code == 200, patch.content
    manager, _ = hire(owner, cmp, "m@example.com", [grant("location.contexts.manage")])
    contexts = post(
        manager, f"/api/c/{cmp}/locations/{location['id']}/contexts", {"type": "rental"}, key()
    )
    assert contexts.status_code == 201, contexts.content


def test_policies_of_sections_need_their_rights(owner: Client, cmp: str) -> None:
    settings, _ = hire(owner, cmp, "s@example.com", [grant("company.settings")])
    rental, _ = hire(owner, cmp, "r@example.com", [grant("rental.policy.manage")])
    tasks, _ = hire(owner, cmp, "t@example.com", [grant("tasks.policy.manage")])
    body = {"enabled": False, "after_minutes": None}
    for client, status in ((settings, 403), (rental, 200)):
        response = client.put(
            f"/api/c/{cmp}/rental-policy", data=body, content_type="application/json", headers=key()
        )
        assert response.status_code == status, response.content
    policy = {"executor_may_release": True, "executor_may_change_due": True}
    for client, status in ((settings, 403), (rental, 403), (tasks, 200)):
        version = owner.get(f"/api/c/{cmp}/tasks/policy").json()["policy"].get("version", 0)
        response = client.put(
            f"/api/c/{cmp}/tasks/policy",
            data={**policy, "expected_version": version},
            content_type="application/json",
            headers=key(),
        )
        assert response.status_code == status, response.content
    assert settings.get(f"/api/c/{cmp}/notification-rules").status_code == 403
    notifier, _ = hire(owner, cmp, "n@example.com", [grant("notifications.policy.manage")])
    assert notifier.get(f"/api/c/{cmp}/notification-rules").status_code == 200
    assert settings.get(f"/api/c/{cmp}/document-requirements").status_code == 403
    documents, _ = hire(owner, cmp, "d@example.com", [grant("documents.policy.manage")])
    assert documents.get(f"/api/c/{cmp}/document-requirements").status_code == 200


def test_link_requests_need_their_own_right(owner: Client, cmp: str) -> None:
    card = post(
        owner,
        f"/api/c/{cmp}/clients",
        {
            "kind": "person",
            "first_name": "Ivan",
            "last_name": "Orlov",
            "contacts": [{"kind": "email", "value": "ivan@example.com"}],
        },
        key(),
    ).json()["client"]
    editor, _ = hire(owner, cmp, "e@example.com", [grant("client.edit"), grant("client.find")])
    body = {"card_type": "client", "card_id": card["id"], "target": "someone@example.com"}
    refused = post(editor, f"/api/c/{cmp}/profile-links", body, key())
    assert refused.status_code == 403
    assert refused.json()["error"]["params"].get("permission", "client.link.request") == (
        "client.link.request"
    )
    requester, _ = hire(owner, cmp, "r@example.com", [grant("client.link.request")])
    assert post(requester, f"/api/c/{cmp}/profile-links", body, key()).status_code == 201


# --- Journal of an employee (§ 23.10.2) --------------------------------------------------------


def test_journal_filters_by_period_section_and_action(owner: Client, cmp: str) -> None:
    _c, worker = hire(owner, cmp, "w@example.com", [])
    owner_employment = Employment.objects.get(position__is_top_authority=True)
    _new_set(owner, cmp, name="J1")
    _rights(owner, cmp, worker, [RENTAL])
    path = f"/api/c/{cmp}/employees/{owner_employment.id}/journal"
    sets = owner.get(f"{path}?section=permission_set").json()["journal"]
    assert [e["action"] for e in sets] == ["permission_set.created"]
    assert sets[0]["object"]["type"] == "permission_set"
    changed = owner.get(f"{path}?action=employee.rights_changed").json()["journal"]
    assert [e["object"] for e in changed] == [{"type": "employment", "id": worker}]
    later = (timezone.now() + timedelta(hours=1)).isoformat()
    assert owner.get(f"{path}?from={later.replace('+', '%2B')}").json()["journal"] == []
