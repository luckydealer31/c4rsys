"""HTTP API of the organisation structure (ТЗ § 5.2, § 23.3.9).

Changing departments and subordination needs `org.manage`; reading the structure needs any
right to manage employees (PROPOSAL, like the employee cards).
"""

from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from c4rsys.companies import orgstructure, staff
from c4rsys.companies.staff_views import EMPLOYEE_VIEW_PERMISSIONS, _company_actor
from c4rsys.companies.views import run_and_respond
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext, CommandResult
from c4rsys.platform.http import get_int, get_str, parse_json

DEPARTMENT_NAME_MAX = 150


def _employees(company_id: UUID) -> list[dict[str, Any]]:
    names = staff.employee_names(company_id, {e.id for e in staff.employments_of(company_id)})
    return [
        {
            "id": str(e.id),
            "name": names.get(e.id, ""),
            "state": e.state,
            "position": {
                "id": str(e.position_id),
                "code": e.position.code,
                "name": e.position.name,
            },
            "is_top_authority": e.position.is_top_authority,
            "linked": e.user_id is not None,
        }
        for e in staff.employments_of(company_id)
        if e.state != "terminated"
    ]


def _body(company_id: UUID) -> dict[str, Any]:
    return {"org": {**orgstructure.structure(company_id), "employees": _employees(company_id)}}


def _parent(data: dict[str, Any]) -> orgstructure.Node | None:
    value = data.get("parent")
    return None if value is None else orgstructure.parse_node(value, "parent")


@require_GET
def org(request: HttpRequest, cmp: str) -> JsonResponse:
    context, _actor = _company_actor(request, cmp, *EMPLOYEE_VIEW_PERMISSIONS)
    return JsonResponse(_body(context.company.id))


@require_POST
def department_create(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _company_actor(request, cmp, "org.manage")
    data = parse_json(request)
    name = get_str(data, "name", max_length=DEPARTMENT_NAME_MAX)
    parent = _parent(data)

    def handler(ctx: CommandContext) -> CommandResult:
        orgstructure.create_department(ctx, context.company.id, name, parent)
        return CommandResult(201, _body(context.company.id))

    payload = {"name": name, "parent": parent.as_dict() if parent else None}
    return run_and_respond(request, "org.department_create", actor, payload, handler)


@require_http_methods(["PATCH"])
def department_detail(request: HttpRequest, cmp: str, department_id: UUID) -> JsonResponse:
    context, actor = _company_actor(request, cmp, "org.manage")
    data = parse_json(request)
    expected_version = get_int(data, "expected_version")
    name = get_str(data, "name", max_length=DEPARTMENT_NAME_MAX) if "name" in data else None
    parent = _parent(data)
    if name is None and parent is None:
        raise errors.validation_error("body", code="NOTHING_TO_CHANGE")

    def handler(ctx: CommandContext) -> CommandResult:
        orgstructure.update_department(
            ctx,
            context.company.id,
            department_id,
            name=name,
            parent=parent,
            expected_version=expected_version,
        )
        return CommandResult(200, _body(context.company.id))

    payload = {
        "department_id": str(department_id),
        "name": name,
        "parent": parent.as_dict() if parent else None,
        "expected_version": expected_version,
    }
    return run_and_respond(request, "org.department_change", actor, payload, handler)


@require_POST
def department_archive(request: HttpRequest, cmp: str, department_id: UUID) -> JsonResponse:
    context, actor = _company_actor(request, cmp, "org.manage")
    expected_version = get_int(parse_json(request), "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        orgstructure.archive_department(ctx, context.company.id, department_id, expected_version)
        return CommandResult(200, _body(context.company.id))

    payload = {"department_id": str(department_id), "expected_version": expected_version}
    return run_and_respond(request, "org.department_archive", actor, payload, handler)


def _employment_id(data: dict[str, Any]) -> UUID:
    try:
        return UUID(get_str(data, "employment_id", max_length=36))
    except ValueError as exc:
        raise errors.validation_error("employment_id") from exc


@require_POST
def member_add(request: HttpRequest, cmp: str, department_id: UUID) -> JsonResponse:
    context, actor = _company_actor(request, cmp, "org.manage")
    employment_id = _employment_id(parse_json(request))

    def handler(ctx: CommandContext) -> CommandResult:
        orgstructure.add_member(ctx, context.company.id, department_id, employment_id)
        return CommandResult(200, _body(context.company.id))

    payload = {"department_id": str(department_id), "employment_id": str(employment_id)}
    return run_and_respond(request, "org.member_add", actor, payload, handler)


@require_POST
def member_remove(
    request: HttpRequest, cmp: str, department_id: UUID, employment_id: UUID
) -> JsonResponse:
    context, actor = _company_actor(request, cmp, "org.manage")

    def handler(ctx: CommandContext) -> CommandResult:
        orgstructure.remove_member(ctx, context.company.id, department_id, employment_id)
        return CommandResult(200, _body(context.company.id))

    payload = {"department_id": str(department_id), "employment_id": str(employment_id)}
    return run_and_respond(request, "org.member_remove", actor, payload, handler)
