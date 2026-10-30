"""Organisation structure: departments, responsibles and subordination (ТЗ § 5.2, § 23.3.9).

* A department is subordinate to another department or to a manager (§ 5.2.1).
* An employee has one or more responsibles: employees or departments. The creator is at the top;
  a new employee is subordinate to the current top manager; links may be changed afterwards.
  The top manager has no responsible: the top changes only by § 3.4.6 (§ 5.2.4).
* Self-subordination and cycles between employees and departments are refused (§ 5.2.4).
* Before a responsible is disabled (blocked or terminated) his subordinates must keep active
  responsibles; otherwise a replacement is assigned first (§ 5.2.4).
* The structure gives no rights (§ 5.2.1); it decides who is told about the overdue tasks of
  a subordinate (§ 5.2.3): `supervisors`.

Every change locks the company row (FOR NO KEY UPDATE): changes of the structure are serialised,
so a cycle check reads a graph nobody changes meanwhile.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.db.models import Q
from django.utils import timezone

from c4rsys.companies.models import (
    Company,
    Department,
    DepartmentMember,
    Employment,
    Responsibility,
)
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext

EMPLOYEE = "employee"
DEPARTMENT = "department"
NOT_TERMINATED = (Employment.State.INVITED, Employment.State.ACTIVE, Employment.State.BLOCKED)


@dataclass(frozen=True)
class Node:
    """An employee card or a department in the structure."""

    type: str  # EMPLOYEE | DEPARTMENT
    id: UUID

    def as_dict(self) -> dict[str, str]:
        return {"type": self.type, "id": str(self.id)}


def parse_node(value: Any, field: str) -> Node:
    if not isinstance(value, dict) or value.get("type") not in (EMPLOYEE, DEPARTMENT):
        raise errors.validation_error(field)
    try:
        return Node(str(value["type"]), UUID(str(value.get("id"))))
    except ValueError as exc:
        raise errors.validation_error(field) from exc


def lock(company_id: UUID) -> None:
    Company.objects.select_for_update(no_key=True).filter(pk=company_id).first()


def top_employment(company_id: UUID) -> Employment | None:
    """The current top manager (§ 3.4.5)."""
    return (
        Employment.objects.filter(
            company_id=company_id,
            state=Employment.State.ACTIVE,
            position__is_top_authority=True,
        )
        .order_by("created_at")
        .first()
    )


def _links(company_id: UUID) -> list[Responsibility]:
    return list(Responsibility.objects.filter(company_id=company_id, ended_at__isnull=True))


def _parents(company_id: UUID, skip_subordinate: UUID | None = None) -> dict[Node, set[Node]]:
    """Node → the nodes it is directly subordinate to, from the current links."""
    graph: dict[Node, set[Node]] = {}
    for link in _links(company_id):
        if link.subordinate_id == skip_subordinate:
            continue
        child = Node(EMPLOYEE, link.subordinate_id)
        graph.setdefault(child, set()).add(_responsible_of(link))
    for d in Department.objects.filter(company_id=company_id, archived_at__isnull=True):
        parent = (
            Node(DEPARTMENT, d.parent_department_id)
            if d.parent_department_id
            else Node(EMPLOYEE, d.parent_employment_id)  # type: ignore[arg-type]
        )
        graph.setdefault(Node(DEPARTMENT, d.id), set()).add(parent)
    return graph


def _responsible_of(link: Responsibility) -> Node:
    if link.responsible_department_id is not None:
        return Node(DEPARTMENT, link.responsible_department_id)
    return Node(EMPLOYEE, link.responsible_employment_id)  # type: ignore[arg-type]


def _reaches(graph: dict[Node, set[Node]], start: Node, target: Node) -> bool:
    """Whether `target` is `start` or above it."""
    seen: set[Node] = set()
    stack = [start]
    while stack:
        node = stack.pop()
        if node == target:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(graph.get(node, ()))
    return False


def _check_parent(graph: dict[Node, set[Node]], child: Node, parent: Node) -> None:
    """§ 5.2.4: no self-subordination and no cycle once `child` is put under `parent`."""
    if child == parent:
        raise errors.business_rule("ORG_SELF_SUBORDINATION")
    if _reaches(graph, parent, child):
        raise errors.business_rule("ORG_CYCLE")


def _employment(company_id: UUID, employment_id: UUID, *, lock_row: bool = False) -> Employment:
    query = Employment.objects.select_related("position", "user").filter(
        company_id=company_id, pk=employment_id
    )
    if lock_row:
        query = query.select_for_update(of=("self",))
    found = query.first()
    if found is None:
        raise errors.not_found()
    return found


def _department(company_id: UUID, department_id: UUID, *, lock_row: bool = False) -> Department:
    query = Department.objects.filter(company_id=company_id, pk=department_id)
    if lock_row:
        query = query.select_for_update()
    found = query.first()
    if found is None:
        raise errors.not_found()
    return found


def _check_target(company_id: UUID, node: Node, field: str) -> None:
    """A new responsible or parent: an active employee or a department that is not archived."""
    if node.type == EMPLOYEE:
        e = Employment.objects.filter(company_id=company_id, pk=node.id).first()
        if e is None:
            raise errors.validation_error(field, code="ORG_NODE_UNKNOWN")
        if e.state != Employment.State.ACTIVE:
            raise errors.business_rule("ORG_RESPONSIBLE_NOT_ACTIVE", field=field)
    else:
        d = Department.objects.filter(company_id=company_id, pk=node.id).first()
        if d is None:
            raise errors.validation_error(field, code="ORG_NODE_UNKNOWN")
        if d.archived_at is not None:
            raise errors.business_rule("ORG_DEPARTMENT_ARCHIVED", field=field)


def _link(
    ctx: CommandContext | None, company_id: UUID, subordinate_id: UUID, node: Node
) -> Responsibility:
    return Responsibility.objects.create(
        company_id=company_id,
        subordinate_id=subordinate_id,
        responsible_employment_id=node.id if node.type == EMPLOYEE else None,
        responsible_department_id=node.id if node.type == DEPARTMENT else None,
        created_by_user_id=ctx.actor.user_id if ctx is not None else None,
    )


def _end(links: Iterable[Responsibility], ctx: CommandContext) -> None:
    now = timezone.now()
    for link in links:
        link.ended_at = now
        link.ended_by_user_id = ctx.actor.user_id
        link.save(update_fields=["ended_at", "ended_by_user_id"])


# --- New employees (§ 5.2.1) ----------------------------------------------------------------


def attach_new(ctx: CommandContext, card: Employment) -> None:
    """A new employee card is subordinate to the current top manager."""
    top = top_employment(card.company_id)
    if top is None or top.pk == card.pk:
        return
    if Responsibility.objects.filter(subordinate=card, ended_at__isnull=True).exists():
        return
    _link(ctx, card.company_id, card.pk, Node(EMPLOYEE, top.pk))
    ctx.audit(
        "org.responsibles_changed",
        object_type="employment",
        object_id=card.pk,
        before={"responsibles": []},
        after={"responsibles": [Node(EMPLOYEE, top.pk).as_dict()]},
        reason="new_employee",
        company_id=card.company_id,
    )


# --- Responsibles of an employee (§ 5.2.1, § 5.2.4) -------------------------------------------


def set_responsibles(
    ctx: CommandContext, company_id: UUID, employment_id: UUID, wanted: list[Node]
) -> None:
    lock(company_id)
    card = _employment(company_id, employment_id, lock_row=True)
    if card.state == Employment.State.TERMINATED:
        raise errors.business_rule("EMPLOYMENT_TERMINATED")
    if card.position.is_top_authority:
        # The top changes only by the procedure of § 3.4.6.
        raise errors.business_rule("ORG_TOP_HAS_NO_RESPONSIBLE")
    if not wanted:
        raise errors.business_rule("ORG_RESPONSIBLE_REQUIRED", field="responsibles")
    if len(set(wanted)) != len(wanted):
        raise errors.validation_error("responsibles", code="ORG_DUPLICATED")
    current = {
        _responsible_of(x): x
        for x in Responsibility.objects.filter(subordinate=card, ended_at__isnull=True)
    }
    graph = _parents(company_id, skip_subordinate=card.pk)
    child = Node(EMPLOYEE, card.pk)
    for index, node in enumerate(wanted):
        field = f"responsibles[{index}]"
        if node not in current:
            _check_target(company_id, node, field)
        _check_parent(graph, child, node)
    removed = [link for node, link in current.items() if node not in wanted]
    added = [node for node in wanted if node not in current]
    if not removed and not added:
        return
    _end(removed, ctx)
    for node in added:
        _link(ctx, company_id, card.pk, node)
    ctx.audit(
        "org.responsibles_changed",
        object_type="employment",
        object_id=card.pk,
        before={"responsibles": [n.as_dict() for n in current]},
        after={"responsibles": [n.as_dict() for n in wanted]},
    )


# --- Departments (§ 5.2.1) ------------------------------------------------------------------


def _parent_fields(parent: Node) -> dict[str, UUID | None]:
    return {
        "parent_department_id": parent.id if parent.type == DEPARTMENT else None,
        "parent_employment_id": parent.id if parent.type == EMPLOYEE else None,
    }


def _parent_of(d: Department) -> Node:
    if d.parent_department_id is not None:
        return Node(DEPARTMENT, d.parent_department_id)
    return Node(EMPLOYEE, d.parent_employment_id)  # type: ignore[arg-type]


def create_department(
    ctx: CommandContext, company_id: UUID, name: str, parent: Node | None
) -> Department:
    """Without a chosen parent the department is put under the current top manager
    (PROPOSAL, like a new employee)."""
    lock(company_id)
    if parent is None:
        top = top_employment(company_id)
        if top is None:
            raise errors.business_rule("ORG_PARENT_REQUIRED", field="parent")
        parent = Node(EMPLOYEE, top.pk)
    _check_target(company_id, parent, "parent")
    department = Department.objects.create(
        company_id=company_id, name=name, **_parent_fields(parent)
    )
    ctx.audit(
        "org.department_created",
        object_type="department",
        object_id=department.id,
        after={"name": name, "parent": parent.as_dict()},
    )
    return department


def update_department(
    ctx: CommandContext,
    company_id: UUID,
    department_id: UUID,
    *,
    name: str | None,
    parent: Node | None,
    expected_version: int,
) -> Department:
    lock(company_id)
    department = _department(company_id, department_id, lock_row=True)
    if department.archived_at is not None:
        raise errors.business_rule("ORG_DEPARTMENT_ARCHIVED")
    if department.version != expected_version:
        raise errors.version_conflict()
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    if name is not None and name != department.name:
        before["name"], after["name"] = department.name, name
        department.name = name
    if parent is not None and parent != _parent_of(department):
        _check_target(company_id, parent, "parent")
        graph = _parents(company_id)
        node = Node(DEPARTMENT, department.id)
        graph[node] = set()
        _check_parent(graph, node, parent)
        before["parent"], after["parent"] = _parent_of(department).as_dict(), parent.as_dict()
        for key, value in _parent_fields(parent).items():
            setattr(department, key, value)
    if not after:
        return department
    department.version += 1
    department.save()
    ctx.audit(
        "org.department_changed",
        object_type="department",
        object_id=department.id,
        before=before,
        after=after,
    )
    return department


def _members(department_id: UUID) -> list[DepartmentMember]:
    return list(
        DepartmentMember.objects.filter(department_id=department_id, removed_at__isnull=True)
    )


def archive_department(
    ctx: CommandContext, company_id: UUID, department_id: UUID, expected_version: int
) -> Department:
    """A department with active child departments, or that is the last active responsible of
    an employee, is not archived (§ 5.2.4). The members leave it; the history stays."""
    lock(company_id)
    department = _department(company_id, department_id, lock_row=True)
    if department.archived_at is not None:
        raise errors.business_rule("ORG_DEPARTMENT_ARCHIVED")
    if department.version != expected_version:
        raise errors.version_conflict()
    if Department.objects.filter(parent_department=department, archived_at__isnull=True).exists():
        raise errors.business_rule("ORG_DEPARTMENT_HAS_CHILDREN")
    node = Node(DEPARTMENT, department.id)
    left = _without_responsible(company_id, node)
    if left:
        raise errors.business_rule("ORG_SUBORDINATES_WITHOUT_RESPONSIBLE", count=len(left))
    now = timezone.now()
    _end(
        Responsibility.objects.filter(responsible_department=department, ended_at__isnull=True),
        ctx,
    )
    for member in _members(department.id):
        member.removed_at = now
        member.removed_by_user_id = ctx.actor.user_id
        member.save(update_fields=["removed_at", "removed_by_user_id"])
    department.archived_at = now
    department.version += 1
    department.save(update_fields=["archived_at", "version"])
    ctx.audit("org.department_archived", object_type="department", object_id=department.id)
    return department


def add_member(
    ctx: CommandContext, company_id: UUID, department_id: UUID, employment_id: UUID
) -> None:
    lock(company_id)
    department = _department(company_id, department_id, lock_row=True)
    if department.archived_at is not None:
        raise errors.business_rule("ORG_DEPARTMENT_ARCHIVED")
    card = _employment(company_id, employment_id)
    if card.state == Employment.State.TERMINATED:
        raise errors.business_rule("EMPLOYMENT_TERMINATED")
    if DepartmentMember.objects.filter(
        department=department, employment=card, removed_at__isnull=True
    ).exists():
        raise errors.business_rule("ORG_ALREADY_MEMBER")
    DepartmentMember.objects.create(
        company_id=company_id,
        department=department,
        employment=card,
        added_by_user_id=ctx.actor.user_id,
    )
    ctx.audit(
        "org.member_added",
        object_type="department",
        object_id=department.id,
        after={"employment": str(card.pk)},
    )


def remove_member(
    ctx: CommandContext, company_id: UUID, department_id: UUID, employment_id: UUID
) -> None:
    lock(company_id)
    department = _department(company_id, department_id, lock_row=True)
    member = (
        DepartmentMember.objects.select_for_update()
        .filter(department=department, employment_id=employment_id, removed_at__isnull=True)
        .first()
    )
    if member is None:
        raise errors.not_found()
    member.removed_at = timezone.now()
    member.removed_by_user_id = ctx.actor.user_id
    member.save(update_fields=["removed_at", "removed_by_user_id"])
    ctx.audit(
        "org.member_removed",
        object_type="department",
        object_id=department.id,
        before={"employment": str(employment_id)},
    )


# --- Disabling a responsible (§ 5.2.4, § 5.3) -----------------------------------------------


def _is_active_responsible(link: Responsibility, leaving: Node) -> bool:
    node = _responsible_of(link)
    if node == leaving:
        return False
    if node.type == DEPARTMENT:
        return Department.objects.filter(pk=node.id, archived_at__isnull=True).exists()
    return Employment.objects.filter(pk=node.id, state=Employment.State.ACTIVE).exists()


def _without_responsible(company_id: UUID, leaving: Node) -> list[str]:
    """Subordinates that would keep no active responsible once `leaving` is disabled: employees
    and departments directly under it."""
    field = "responsible_employment" if leaving.type == EMPLOYEE else "responsible_department"
    found: list[str] = []
    subordinates = Responsibility.objects.filter(
        company_id=company_id,
        ended_at__isnull=True,
        subordinate__state__in=NOT_TERMINATED,
        **{field: leaving.id},
    ).values_list("subordinate_id", flat=True)
    for subordinate_id in set(subordinates):
        others = Responsibility.objects.filter(subordinate_id=subordinate_id, ended_at__isnull=True)
        if not any(_is_active_responsible(x, leaving) for x in others):
            found.append(str(subordinate_id))
    if leaving.type == EMPLOYEE:
        found += [
            str(d)
            for d in Department.objects.filter(
                parent_employment_id=leaving.id, archived_at__isnull=True
            ).values_list("id", flat=True)
        ]
    return found


def check_can_disable(company_id: UUID, employment_id: UUID) -> None:
    """Called under `lock` before blocking or terminating an employee."""
    left = _without_responsible(company_id, Node(EMPLOYEE, employment_id))
    if left:
        raise errors.business_rule("ORG_SUBORDINATES_WITHOUT_RESPONSIBLE", count=len(left))


def on_terminated(ctx: CommandContext, card: Employment) -> None:
    """A terminated employee leaves the structure: his own links, the links to him and his
    memberships end; the history stays. Subordinates or departments left without an active
    responsible by a system termination (§ 3.4.4, § 3.4.6) go to the top manager (PROPOSAL)."""
    links = Responsibility.objects.filter(
        Q(subordinate=card) | Q(responsible_employment=card), ended_at__isnull=True
    )
    _end(links, ctx)
    now = timezone.now()
    DepartmentMember.objects.filter(employment=card, removed_at__isnull=True).update(
        removed_at=now, removed_by_user_id=ctx.actor.user_id
    )
    reattach_orphans(ctx, card.company_id)


def reattach_orphans(ctx: CommandContext, company_id: UUID) -> None:
    top = top_employment(company_id)
    if top is None:
        return
    # The top manager is at the top: he has no responsible (§ 5.2.1).
    _end(Responsibility.objects.filter(subordinate=top, ended_at__isnull=True), ctx)
    top_node = Node(EMPLOYEE, top.pk)
    for card in Employment.objects.filter(company_id=company_id, state__in=NOT_TERMINATED).exclude(
        pk=top.pk
    ):
        links = list(Responsibility.objects.filter(subordinate=card, ended_at__isnull=True))
        if links:
            continue
        _link(ctx, company_id, card.pk, top_node)
        ctx.audit(
            "org.responsibles_changed",
            object_type="employment",
            object_id=card.pk,
            before={"responsibles": []},
            after={"responsibles": [top_node.as_dict()]},
            reason="responsible_left",
            company_id=company_id,
        )
    for department in Department.objects.filter(
        company_id=company_id, archived_at__isnull=True, parent_employment__isnull=False
    ).exclude(parent_employment__state__in=NOT_TERMINATED):
        before = _parent_of(department).as_dict()
        department.parent_employment_id = top.pk
        department.version += 1
        department.save(update_fields=["parent_employment", "version"])
        ctx.audit(
            "org.department_changed",
            object_type="department",
            object_id=department.id,
            before={"parent": before},
            after={"parent": top_node.as_dict()},
            reason="responsible_left",
            company_id=company_id,
        )


# --- Recipients of the overdue notices (§ 5.2.3, § 20.2.1) -----------------------------------


def supervisors(company_id: UUID, employment_id: UUID) -> list[UUID]:
    """Every assigned responsible of the employee; for a responsible department every active
    employee of that department."""
    found: set[UUID] = set()
    for link in Responsibility.objects.filter(
        company_id=company_id, subordinate_id=employment_id, ended_at__isnull=True
    ):
        if link.responsible_employment_id is not None:
            found.add(link.responsible_employment_id)
        elif link.responsible_department_id is not None:
            found |= set(
                DepartmentMember.objects.filter(
                    department_id=link.responsible_department_id,
                    department__archived_at__isnull=True,
                    removed_at__isnull=True,
                ).values_list("employment_id", flat=True)
            )
    active = Employment.objects.filter(
        pk__in=found, state=Employment.State.ACTIVE, user__isnull=False
    ).values_list("id", flat=True)
    return sorted(set(active) - {employment_id})


# --- Reading ------------------------------------------------------------------------------------


def responsibles_payload(employment_id: UUID) -> list[dict[str, str]]:
    return [
        _responsible_of(x).as_dict()
        for x in Responsibility.objects.filter(
            subordinate_id=employment_id, ended_at__isnull=True
        ).order_by("created_at")
    ]


def departments_of(employment_id: UUID) -> list[dict[str, str]]:
    return [
        {"id": str(m.department_id), "name": m.department.name}
        for m in DepartmentMember.objects.select_related("department").filter(
            employment_id=employment_id, removed_at__isnull=True
        )
    ]


def structure(company_id: UUID) -> dict[str, Any]:
    """Current departments with their parent and members and the current links."""
    members: dict[UUID, list[str]] = {}
    for m in DepartmentMember.objects.filter(company_id=company_id, removed_at__isnull=True):
        members.setdefault(m.department_id, []).append(str(m.employment_id))
    departments = [
        {
            "id": str(d.id),
            "name": d.name,
            "parent": _parent_of(d).as_dict(),
            "members": members.get(d.id, []),
            "version": d.version,
        }
        for d in Department.objects.filter(
            company_id=company_id, archived_at__isnull=True
        ).order_by("name")
    ]
    links = [
        {"subordinate": str(x.subordinate_id), "responsible": _responsible_of(x).as_dict()}
        for x in Responsibility.objects.filter(
            company_id=company_id, ended_at__isnull=True
        ).order_by("created_at")
    ]
    top = top_employment(company_id)
    return {
        "top": str(top.pk) if top else None,
        "departments": departments,
        "links": links,
    }
