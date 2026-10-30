"""Block 10, part B: positions, the employee card, departments and responsibles, overdue notices
to responsibles, termination of access, profile history and public fields (ТЗ § 3.1.8, § 3.5.2,
§ 3.5.3, § 5.1.4–5.1.7, § 5.2, § 5.3, § 23.3.1, § 23.3.9)."""

from datetime import timedelta
from typing import Any

import pytest
from django.db import IntegrityError, transaction
from django.test import Client
from django.utils import timezone

from c4rsys.companies.models import Company, Employment, Responsibility
from c4rsys.platform.models import AuditEvent
from c4rsys.tasks import notices
from tests.accounts.helpers import post
from tests.companies.helpers import create_company, hire, key, named_user, patch_company

pytestmark = pytest.mark.django_db

ADMIN = [{"type": "set", "set": "company_admin"}]
MECHANIC = [{"type": "set", "set": "mechanic"}]


def _grant(*codes: str) -> list[dict[str, Any]]:
    return [{"type": "grant", "permission": c} for c in codes]


@pytest.fixture
def cmp(owner: Client) -> str:
    public_id: str = create_company(owner, timezone="Asia/Bangkok").json()["company"]["public_id"]
    return public_id


def _me(client: Client, cmp: str) -> str:
    employment_id: str = client.get(f"/api/c/{cmp}").json()["company"]["employment"]["id"]
    return employment_id


def _card(client: Client, cmp: str, employment_id: str) -> dict[str, Any]:
    response = client.get(f"/api/c/{cmp}/employees/{employment_id}")
    assert response.status_code == 200, response.content
    card: dict[str, Any] = response.json()["employee"]
    return card


def _code(response: Any) -> str:
    code: str = response.json()["error"]["code"]
    return code


def _put(client: Client, path: str, data: dict[str, Any]) -> Any:
    return client.put(path, data=data, content_type="application/json", headers=key())


def _patch(client: Client, path: str, data: dict[str, Any]) -> Any:
    return client.patch(path, data=data, content_type="application/json", headers=key())


def _responsibles(
    client: Client, cmp: str, employment_id: str, nodes: list[tuple[str, str]]
) -> Any:
    body = {"responsibles": [{"type": t, "id": i} for t, i in nodes]}
    return _put(client, f"/api/c/{cmp}/employees/{employment_id}/responsibles", body)


def _department(client: Client, cmp: str, name: str, parent: tuple[str, str] | None = None) -> str:
    body: dict[str, Any] = {"name": name}
    if parent is not None:
        body["parent"] = {"type": parent[0], "id": parent[1]}
    response = post(client, f"/api/c/{cmp}/org/departments", body, key())
    assert response.status_code == 201, response.content
    found: str = next(d["id"] for d in response.json()["org"]["departments"] if d["name"] == name)
    return found


def _org(client: Client, cmp: str) -> dict[str, Any]:
    org: dict[str, Any] = client.get(f"/api/c/{cmp}/org").json()["org"]
    return org


def _add_member(client: Client, cmp: str, department_id: str, employment_id: str) -> Any:
    return post(
        client,
        f"/api/c/{cmp}/org/departments/{department_id}/members",
        {"employment_id": employment_id},
        key(),
    )


# --- Hierarchy (§ 5.2.1, § 5.2.4) -------------------------------------------------------------


def test_new_employee_is_under_the_top_manager(owner: Client, cmp: str) -> None:
    top = _me(owner, cmp)
    _client, staff_id = hire(owner, cmp, "a@example.com", [])
    assert _card(owner, cmp, staff_id)["responsibles"] == [{"type": "employee", "id": top}]
    org = _org(owner, cmp)
    assert org["top"] == top
    assert {"subordinate": staff_id, "responsible": {"type": "employee", "id": top}} in org["links"]
    # The top changes only by § 3.4.6; he has no responsible.
    refused = _responsibles(owner, cmp, top, [("employee", staff_id)])
    assert _code(refused) == "ORG_TOP_HAS_NO_RESPONSIBLE"


def test_self_subordination_and_cycles_are_refused(owner: Client, cmp: str) -> None:
    _a, a_id = hire(owner, cmp, "a@example.com", [])
    _b, b_id = hire(owner, cmp, "b@example.com", [])
    assert _code(_responsibles(owner, cmp, a_id, [("employee", a_id)])) == "ORG_SELF_SUBORDINATION"
    assert _responsibles(owner, cmp, b_id, [("employee", a_id)]).status_code == 200
    # a under b while b is under a — a cycle.
    assert _code(_responsibles(owner, cmp, a_id, [("employee", b_id)])) == "ORG_CYCLE"
    # Through a department: the department is under b, and a would be under the department.
    department = _department(owner, cmp, "Workshop", ("employee", b_id))
    assert _code(_responsibles(owner, cmp, a_id, [("department", department)])) == "ORG_CYCLE"
    # A department cannot move under its own descendant.
    child = _department(owner, cmp, "Tyres", ("department", department))
    version = next(d for d in _org(owner, cmp)["departments"] if d["id"] == department)["version"]
    moved = _patch(
        owner,
        f"/api/c/{cmp}/org/departments/{department}",
        {"parent": {"type": "department", "id": child}, "expected_version": version},
    )
    assert _code(moved) == "ORG_CYCLE"
    own = _patch(
        owner,
        f"/api/c/{cmp}/org/departments/{department}",
        {"parent": {"type": "department", "id": department}, "expected_version": version},
    )
    assert _code(own) == "ORG_SELF_SUBORDINATION"
    # The database refuses self-subordination too.
    company_id = Employment.objects.get(pk=a_id).company_id
    with pytest.raises(IntegrityError), transaction.atomic():
        Responsibility.objects.create(
            company_id=company_id, subordinate_id=a_id, responsible_employment_id=a_id
        )


def test_several_responsibles_and_one_is_required(owner: Client, cmp: str) -> None:
    top = _me(owner, cmp)
    _a, a_id = hire(owner, cmp, "a@example.com", [])
    _b, b_id = hire(owner, cmp, "b@example.com", [])
    department = _department(owner, cmp, "Sales")
    ok = _responsibles(owner, cmp, b_id, [("employee", a_id), ("department", department)])
    assert ok.status_code == 200, ok.content
    assert ok.json()["employee"]["responsibles"] == [
        {"type": "employee", "id": a_id},
        {"type": "department", "id": department},
    ]
    assert _code(_responsibles(owner, cmp, b_id, [])) == "ORG_RESPONSIBLE_REQUIRED"
    # The departments without a chosen parent go under the top manager (PROPOSAL).
    assert next(d for d in _org(owner, cmp)["departments"] if d["id"] == department)["parent"] == {
        "type": "employee",
        "id": top,
    }
    history = AuditEvent.objects.filter(action="org.responsibles_changed", object_id=b_id)
    assert history.filter(reason="").count() == 1


def test_org_structure_needs_its_right(owner: Client, cmp: str) -> None:
    clerk, _clerk_id = hire(owner, cmp, "c@example.com", _grant("employee.invite"))
    _a, a_id = hire(owner, cmp, "a@example.com", [])
    _b, b_id = hire(owner, cmp, "b@example.com", [])
    assert clerk.get(f"/api/c/{cmp}/org").status_code == 200
    assert _responsibles(clerk, cmp, b_id, [("employee", a_id)]).status_code == 403
    denied = post(clerk, f"/api/c/{cmp}/org/departments", {"name": "X"}, key())
    assert denied.status_code == 403
    manager, _ = hire(owner, cmp, "m@example.com", _grant("org.manage"))
    assert _responsibles(manager, cmp, b_id, [("employee", a_id)]).status_code == 200
    # The place in the structure gives no rights (§ 5.2.1): a is now b's responsible and still
    # cannot see the employee cards.
    a_client = _a
    assert a_client.get(f"/api/c/{cmp}/employees/{b_id}").status_code == 403


def test_disabling_the_last_responsible_is_refused(owner: Client, cmp: str) -> None:
    top = _me(owner, cmp)
    _a, a_id = hire(owner, cmp, "a@example.com", [])
    _b, b_id = hire(owner, cmp, "b@example.com", [])
    assert _responsibles(owner, cmp, b_id, [("employee", a_id)]).status_code == 200
    blocked = post(owner, f"/api/c/{cmp}/employees/{a_id}/block", {}, key())
    assert _code(blocked) == "ORG_SUBORDINATES_WITHOUT_RESPONSIBLE"
    terminated = post(owner, f"/api/c/{cmp}/employees/{a_id}/terminate", {}, key())
    assert _code(terminated) == "ORG_SUBORDINATES_WITHOUT_RESPONSIBLE"
    # A department under a as manager is a subordinate too.
    assert (
        _responsibles(owner, cmp, b_id, [("employee", a_id), ("employee", top)]).status_code == 200
    )
    department = _department(owner, cmp, "Desk", ("employee", a_id))
    terminated = post(owner, f"/api/c/{cmp}/employees/{a_id}/terminate", {}, key())
    assert _code(terminated) == "ORG_SUBORDINATES_WITHOUT_RESPONSIBLE"
    version = next(d for d in _org(owner, cmp)["departments"] if d["id"] == department)["version"]
    moved = _patch(
        owner,
        f"/api/c/{cmp}/org/departments/{department}",
        {"parent": {"type": "employee", "id": top}, "expected_version": version},
    )
    assert moved.status_code == 200, moved.content
    # With a replacement assigned first, a may leave; b keeps the top manager.
    assert post(owner, f"/api/c/{cmp}/employees/{a_id}/block", {}, key()).status_code == 200


def test_archiving_a_department_keeps_responsibles(owner: Client, cmp: str) -> None:
    _a, a_id = hire(owner, cmp, "a@example.com", [])
    department = _department(owner, cmp, "Night shift")
    child = _department(owner, cmp, "Inner", ("department", department))
    assert _responsibles(owner, cmp, a_id, [("department", department)]).status_code == 200
    version = next(d for d in _org(owner, cmp)["departments"] if d["id"] == department)["version"]
    url = f"/api/c/{cmp}/org/departments/{department}/archive"
    assert _code(post(owner, url, {"expected_version": version}, key())) == (
        "ORG_DEPARTMENT_HAS_CHILDREN"
    )
    child_version = next(d for d in _org(owner, cmp)["departments"] if d["id"] == child)["version"]
    archived = post(
        owner,
        f"/api/c/{cmp}/org/departments/{child}/archive",
        {"expected_version": child_version},
        key(),
    )
    assert archived.status_code == 200
    assert _code(post(owner, url, {"expected_version": version}, key())) == (
        "ORG_SUBORDINATES_WITHOUT_RESPONSIBLE"
    )


# --- Overdue notices to the responsibles (§ 5.2.3) -------------------------------------------


def _overdue_task(owner: Client, cmp: str, assignee: str) -> dict[str, Any]:
    past = (timezone.localdate() - timedelta(days=1)).isoformat()
    body = {"title": "Return the keys", "assign": assignee, "due_date": past}
    response = post(owner, f"/api/c/{cmp}/tasks", body, key())
    assert response.status_code == 201, response.content
    task: dict[str, Any] = response.json()["task"]
    return task


def _overdue_notice(client: Client) -> dict[str, Any] | None:
    items = client.get("/api/notifications").json()["notifications"]
    return next((n for n in items if n["event"] == "task.overdue"), None)


def test_overdue_notice_reaches_responsibles_and_department(owner: Client, cmp: str) -> None:
    worker, worker_id = hire(owner, cmp, "w@example.com", MECHANIC)
    boss, boss_id = hire(owner, cmp, "boss@example.com", _grant("tasks.view_all"))
    x, x_id = hire(owner, cmp, "x@example.com", [])
    y, y_id = hire(owner, cmp, "y@example.com", [])
    department = _department(owner, cmp, "Supervisors")
    for member in (x_id, y_id):
        assert _add_member(owner, cmp, department, member).status_code == 200
    linked = _responsibles(
        owner, cmp, worker_id, [("employee", boss_id), ("department", department)]
    )
    assert linked.status_code == 200, linked.content
    task = _overdue_task(owner, cmp, worker_id)
    notices.run(timezone.now())

    full = _overdue_notice(boss)
    assert full is not None and not full["limited"]
    assert full["object_id"] == task["id"] and full["params"]["title"] == "Return the keys"
    # Every employee of the responsible department, without access to the task: the employee
    # and the fact only.
    for client in (x, y):
        limited = _overdue_notice(client)
        assert limited is not None and limited["limited"] and limited["object_id"] is None
        assert limited["params"] == {"assignee_name": "Anna Petrova"}
        assert limited["state"] == "active"
    assert _overdue_notice(worker) is not None
    # Reading does not end it; the result of the task ends it for everyone (§ 5.2.3).
    item = _overdue_notice(x)
    assert item is not None
    post(x, "/api/notifications/read", {"ids": [item["id"]]}, key())
    item = _overdue_notice(x)
    assert item is not None and item["state"] == "active"
    start = post(
        worker,
        f"/api/c/{cmp}/tasks/{task['id']}/start",
        {"expected_version": task["version"]},
        key(),
    )
    assert start.status_code == 200, start.content
    done = post(
        worker,
        f"/api/c/{cmp}/tasks/{task['id']}/complete",
        {"expected_version": start.json()["task"]["version"]},
        key(),
    )
    assert done.status_code == 200, done.content
    for client in (boss, x, y):
        notice = _overdue_notice(client)
        assert notice is not None and notice["state"] == "done"


# --- Termination of access (§ 5.3) -----------------------------------------------------------


def test_termination_keeps_account_history_and_operations(owner: Client, cmp: str) -> None:
    worker, worker_id = hire(owner, cmp, "w@example.com", MECHANIC)
    created = post(worker, f"/api/c/{cmp}/tasks", {"title": "Wash the van"}, key())
    assert created.status_code == 201, created.content
    task_id = created.json()["task"]["id"]
    clerk, _ = hire(owner, cmp, "c@example.com", _grant("employee.access.block"))
    # A separate right (§ 23.3.1): blocking does not give termination.
    refused = post(clerk, f"/api/c/{cmp}/employees/{worker_id}/terminate", {}, key())
    assert refused.status_code == 403
    response = post(
        owner, f"/api/c/{cmp}/employees/{worker_id}/terminate", {"reason": "Contract ended"}, key()
    )
    assert response.status_code == 200, response.content
    card = response.json()["employee"]
    assert card["state"] == "terminated" and card["termination"]["reason"] == "Contract ended"
    assert card["responsibles"] == []
    # The account stays; the company is no longer accessible.
    assert worker.get("/api/me").status_code == 200
    assert worker.get(f"/api/c/{cmp}").status_code == 404
    # The history keeps the author, the created task is not cancelled.
    assert AuditEvent.objects.filter(actor_employment_id=worker_id, action="task.created").exists()
    task = owner.get(f"/api/c/{cmp}/tasks/{task_id}").json()["task"]
    assert task["state"] == "open"
    event = AuditEvent.objects.get(action="employee.access_terminated", object_id=worker_id)
    assert event.reason == "Contract ended"
    # Rights pass to nobody and stay recorded on the card.
    assert Employment.objects.get(pk=worker_id).state == "terminated"
    again = post(owner, f"/api/c/{cmp}/employees/{worker_id}/terminate", {}, key())
    assert _code(again) == "EMPLOYMENT_STATE_INVALID"
    # The journal of the terminated employee stays readable (§ 23.10.1).
    journal = owner.get(f"/api/c/{cmp}/employees/{worker_id}/journal").json()["journal"]
    assert any(e["action"] == "task.created" for e in journal)


def test_termination_refused_for_self_and_top(owner: Client, cmp: str) -> None:
    top = _me(owner, cmp)
    admin, admin_id = hire(owner, cmp, "adm@example.com", ADMIN)
    assert _code(post(admin, f"/api/c/{cmp}/employees/{admin_id}/terminate", {}, key())) == (
        "SELF_ACCESS_CHANGE_FORBIDDEN"
    )
    assert _code(post(admin, f"/api/c/{cmp}/employees/{top}/terminate", {}, key())) == (
        "EMPLOYEE_IS_TOP_AUTHORITY"
    )


def test_terminate_allowed_in_deactivated_company_but_not_org_changes(
    owner: Client, cmp: str
) -> None:
    _a, a_id = hire(owner, cmp, "a@example.com", [])
    Company.objects.filter(public_id=cmp).update(deactivated_at=timezone.now())
    assert _code(post(owner, f"/api/c/{cmp}/org/departments", {"name": "X"}, key())) == (
        "COMPANY_DEACTIVATED"
    )
    assert post(owner, f"/api/c/{cmp}/employees/{a_id}/terminate", {}, key()).status_code == 200


# --- Positions and the card (§ 5.1.4, § 5.1.5, § 5.1.7) --------------------------------------


def test_positions_handbook_and_card(owner: Client, cmp: str) -> None:
    top = _me(owner, cmp)
    _a, a_id = hire(owner, cmp, "a@example.com", [])
    created = post(owner, f"/api/c/{cmp}/positions", {"name": "Mechanic"}, key())
    assert created.status_code == 201, created.content
    positions = created.json()["positions"]
    mechanic = next(p for p in positions if p["name"] == "Mechanic")
    director = next(p for p in positions if p["code"] == "director")
    assert director["is_dedicated"]
    duplicate = post(owner, f"/api/c/{cmp}/positions", {"name": "mechanic"}, key())
    assert _code(duplicate) == "POSITION_NAME_TAKEN"
    special = _patch(owner, f"/api/c/{cmp}/positions/{director['id']}", {"name": "Boss"})
    assert _code(special) == "POSITION_SPECIAL"
    renamed = _patch(owner, f"/api/c/{cmp}/positions/{mechanic['id']}", {"name": "Senior mechanic"})
    assert renamed.status_code == 200

    card = _card(owner, cmp, a_id)
    changed = _patch(
        owner,
        f"/api/c/{cmp}/employees/{a_id}",
        {
            "position_id": mechanic["id"],
            "work_email": "A.Work@Example.com",
            "work_phone": "+66 81 000 0000",
            "expected_version": card["version"],
        },
    )
    assert changed.status_code == 200, changed.content
    body = changed.json()["employee"]
    assert body["position"]["name"] == "Senior mechanic"
    assert body["work_email"] == "a.work@example.com" and body["work_phone"] == "+66 81 000 0000"
    # The position gives no rights (§ 5.1.4).
    assert body["rights"] == []
    # The dedicated positions are not given and not changed from the card.
    refused = _patch(
        owner,
        f"/api/c/{cmp}/employees/{a_id}",
        {"position_id": director["id"], "expected_version": body["version"]},
    )
    assert _code(refused) == "POSITION_NOT_ASSIGNABLE"
    top_card = _card(owner, cmp, top)
    refused = _patch(
        owner,
        f"/api/c/{cmp}/employees/{top}",
        {"position_id": mechanic["id"], "expected_version": top_card["version"]},
    )
    assert _code(refused) == "EMPLOYEE_POSITION_DEDICATED"


def test_card_rights_are_separate(owner: Client, cmp: str) -> None:
    _a, a_id = hire(owner, cmp, "a@example.com", [])
    editor, _ = hire(owner, cmp, "e@example.com", _grant("employee.card.edit"))
    card = _card(editor, cmp, a_id)
    # A new position from the card also needs the handbook right (PROPOSAL).
    added = _patch(
        editor,
        f"/api/c/{cmp}/employees/{a_id}",
        {"position_name": "Driver", "expected_version": card["version"]},
    )
    assert added.status_code == 403
    assert post(editor, f"/api/c/{cmp}/positions", {"name": "Driver"}, key()).status_code == 403
    contacts = _patch(
        editor,
        f"/api/c/{cmp}/employees/{a_id}",
        {"work_phone": "123", "expected_version": card["version"]},
    )
    assert contacts.status_code == 200
    stale = _patch(
        editor,
        f"/api/c/{cmp}/employees/{a_id}",
        {"work_phone": "456", "expected_version": card["version"]},
    )
    assert stale.status_code == 409
    manager, _ = hire(
        owner, cmp, "m@example.com", _grant("employee.card.edit", "employee.positions.manage")
    )
    fresh = _card(manager, cmp, a_id)
    added = _patch(
        manager,
        f"/api/c/{cmp}/employees/{a_id}",
        {"position_name": "Driver", "expected_version": fresh["version"]},
    )
    assert added.status_code == 200
    assert added.json()["employee"]["position"]["name"] == "Driver"


def test_acceptance_shares_chosen_data(owner: Client, cmp: str) -> None:
    worker = Client()
    named_user(worker, "w@example.com")
    public_id = worker.get("/api/me").json()["user"]["public_id"]
    body = {"target": public_id, "position_name": "Staff", "rights": []}
    invitation = post(owner, f"/api/c/{cmp}/employees/invitations", body, key()).json()
    shown = worker.get("/api/invitations").json()["invitations"][0]["data_given"]
    assert shown == {
        "required": ["first_name", "last_name"],
        "optional": ["middle_name", "photo", "email", "phone"],
    }
    flags = {"share_photo": True, "share_phone": False}
    accepted = post(
        worker, f"/api/invitations/{invitation['invitation']['id']}/accept", flags, key()
    )
    assert accepted.status_code == 200, accepted.content
    employment = Employment.objects.get(pk=invitation["invitation"]["employment_id"])
    assert employment.shares_photo and not employment.shares_phone
    card = _card(owner, cmp, str(employment.id))
    # Not given: hidden; the photo is shown only when the profile has one.
    assert card["user"]["phone"] is None and card["user"]["email"] is None
    assert card["user"]["first_name"] == "Anna"


# --- Profile history and public fields (§ 3.1.8, § 3.5.2, § 3.5.3) --------------------------


def test_profile_history(owner: Client, cmp: str) -> None:
    version = owner.get(f"/api/c/{cmp}").json()["company"]["version"]
    assert (
        patch_company(
            owner, cmp, {"legal_name": "Sunrise Co., Ltd.", "expected_version": version}, "p-9"
        ).status_code
        == 200
    )
    history = owner.get(f"/api/c/{cmp}/profile-history").json()["history"]
    latest = history[0]
    assert latest["action"] == "company.profile_changed"
    assert latest["before"] == {"legal_name": ""}
    assert latest["after"] == {"legal_name": "Sunrise Co., Ltd."}
    assert latest["actor"]["name"] == "Anna Petrova"
    assert history[-1]["action"] == "company.created"
    worker, _ = hire(owner, cmp, "w@example.com", [])
    assert worker.get(f"/api/c/{cmp}/profile-history").status_code == 403


def test_public_fields_in_the_search_card(owner: Client, cmp: str) -> None:
    version = owner.get(f"/api/c/{cmp}").json()["company"]["version"]
    data = {
        "legal_name": "Sunrise Co., Ltd.",
        "phone": "+66 2 000 0000",
        "country": "TH",
        "expected_version": version,
    }
    assert patch_company(owner, cmp, data, "p-10").status_code == 200
    stranger = Client()
    named_user(stranger, "s@example.com")
    card = stranger.get(f"/api/companies/lookup?id={cmp}").json()["company"]
    # The minimum only until a field is made public.
    assert card["display_name"] == "Sunrise Rent" and card["country"] == "TH"
    assert card["public"] == {}

    version = owner.get(f"/api/c/{cmp}").json()["company"]["version"]
    set_public = _put(
        owner, f"/api/c/{cmp}/public-fields", {"fields": ["phone"], "expected_version": version}
    )
    assert set_public.status_code == 200, set_public.content
    assert set_public.json()["company"]["public_fields"] == ["phone"]
    card = stranger.get(f"/api/companies/lookup?id={cmp}").json()["company"]
    assert card["public"] == {"phone": "+66 2 000 0000"}
    assert "legal_name" not in card["public"]
    assert stranger.get("/api/companies/lookup?id=CMP-0000000").status_code == 404
    history = owner.get(f"/api/c/{cmp}/profile-history").json()["history"]
    assert history[0]["action"] == "company.public_fields_changed"

    # Only the top authority or full administrative access (§ 3.1.8).
    settings_only, _ = hire(owner, cmp, "set@example.com", _grant("company.settings"))
    version = owner.get(f"/api/c/{cmp}").json()["company"]["version"]
    refused = _put(
        settings_only, f"/api/c/{cmp}/public-fields", {"fields": [], "expected_version": version}
    )
    assert refused.status_code == 403
    admin, _ = hire(owner, cmp, "adm@example.com", ADMIN)
    allowed = _put(
        admin, f"/api/c/{cmp}/public-fields", {"fields": [], "expected_version": version}
    )
    assert allowed.status_code == 200, allowed.content
    bad = _put(owner, f"/api/c/{cmp}/public-fields", {"fields": ["owners"], "expected_version": 1})
    assert bad.status_code == 400
