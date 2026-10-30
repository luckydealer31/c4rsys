"""XLSX import API (ТЗ § 22.2–22.5): templates, check, decisions, posting, corrected file,
history with source files and cancel of a whole import.

Import needs its own right and the usual right to the section of the data (§ 22.5.1); cancel of
a posted import is a separate right (§ 23.8.4). The routes `/warehouse/imports` of the catalog
stay; the other kinds use `/imports` with `kind`.
"""

import hashlib
from uuid import UUID

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods, require_POST

from c4rsys.accounts.middleware import require_user
from c4rsys.companies import access
from c4rsys.companies import services as companies
from c4rsys.companies.views import run_and_respond
from c4rsys.files import services as files
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import get_int, get_str, parse_json
from c4rsys.warehouse import imports
from c4rsys.warehouse.models import ImportOperation


def _actor(request: HttpRequest, cmp: str) -> tuple[companies.CompanyContext, Actor]:
    user, session = require_user(request)
    context = companies.resolve_company(user, cmp, session.id)
    return context, companies.company_actor(user, context, session.id)


def _has(actor: Actor, permission: str) -> bool:
    return access.RightsResolver().has_permission(actor, permission)


def _require(actor: Actor, *permissions: str) -> None:
    for permission in permissions:
        if not _has(actor, permission):
            raise errors.permission_denied(permission)


def _kind(value: str | None) -> str:
    kind = value or "items"
    if kind not in imports.KINDS:
        raise errors.validation_error("kind")
    return kind


def _for_kind(
    request: HttpRequest, cmp: str, kind: str, permission: str
) -> tuple[companies.CompanyContext, Actor]:
    context, actor = _actor(request, cmp)
    _require(actor, permission, imports.adapter(kind).permission)
    return context, actor


def _operation(company_id: UUID, operation_id: UUID) -> ImportOperation:
    operation = ImportOperation.objects.filter(company_id=company_id, pk=operation_id).first()
    if operation is None:
        raise errors.not_found()
    return operation


def _xlsx(content: bytes, name: str) -> HttpResponse:
    response = HttpResponse(content, content_type=imports.XLSX)
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    return response


def _uploaded(request: HttpRequest) -> tuple[bytes, str]:
    uploaded = request.FILES.get("file")
    if uploaded is None:
        raise errors.validation_error("file", code="FIELD_REQUIRED")
    if uploaded.size is not None and uploaded.size > imports.MAX_FILE_BYTES:
        raise errors.validation_error(
            "file", code="FILE_TOO_LARGE", max_bytes=imports.MAX_FILE_BYTES
        )
    return uploaded.read(), uploaded.name or "import.xlsx"


@require_http_methods(["GET"])
def import_template(request: HttpRequest, cmp: str, kind: str = "items") -> HttpResponse:
    kind = _kind(kind)
    context, _ = _for_kind(request, cmp, kind, "import.run")
    found = imports.adapter(kind)
    return _xlsx(imports.template(kind, context.company.id), f"c4rsys-{kind}-v{found.version}.xlsx")


@require_http_methods(["GET", "POST"])
def import_list(request: HttpRequest, cmp: str, default_kind: str | None = None) -> JsonResponse:
    if request.method == "GET":
        context, actor = _actor(request, cmp)
        _require(actor, "import.history.view")
        # The history shows only the kinds whose section the user may see (§ 22.2.4, § 22.5.1).
        allowed = [k for k in imports.KINDS if _has(actor, imports.adapter(k).permission)]
        found = ImportOperation.objects.filter(company=context.company, kind__in=allowed)
        kind = request.GET.get("kind") or default_kind
        if kind:
            found = found.filter(kind=_kind(kind))
        found = found.order_by("-created_at")
        return JsonResponse({"imports": [imports.payload(o, with_rows=False) for o in found[:100]]})

    kind = _kind(request.POST.get("kind") or default_kind)
    context, actor = _for_kind(request, cmp, kind, "import.run")
    content, name = _uploaded(request)
    imports.parse(content, kind)  # the format is refused before anything is stored

    def handler(ctx: CommandContext) -> CommandResult:
        operation = imports.check(ctx, context.company, content, name, kind)
        return CommandResult(201, {"import": imports.payload(operation)})

    payload = {"kind": kind, "sha256": hashlib.sha256(content).hexdigest(), "file_name": name}
    return run_and_respond(request, "warehouse.import_check", actor, payload, handler)


@require_http_methods(["GET"])
def import_detail(request: HttpRequest, cmp: str, operation_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if not (_has(actor, "import.history.view") or _has(actor, "import.run")):
        raise errors.permission_denied("import.history.view")
    operation = _operation(context.company.id, operation_id)
    _require(actor, imports.adapter(operation.kind).permission)
    return JsonResponse({"import": imports.payload(operation)})


@require_http_methods(["GET"])
def import_file(request: HttpRequest, cmp: str, operation_id: UUID) -> HttpResponse:
    """The source file, for those who may see the history, while it is kept (§ 22.2.4)."""
    context, actor = _actor(request, cmp)
    operation = _operation(context.company.id, operation_id)
    _require(actor, "import.history.view", imports.adapter(operation.kind).permission)
    if operation.file is None:
        raise errors.not_found()
    return _xlsx(files.content(operation.file), operation.file_name or "import.xlsx")


@require_http_methods(["GET"])
def import_correction(request: HttpRequest, cmp: str, operation_id: UUID) -> HttpResponse:
    """The file for correction: rows not loaded with their codes and reasons (§ 22.2.7)."""
    context, actor = _actor(request, cmp)
    operation = _operation(context.company.id, operation_id)
    _require(actor, "import.run", imports.adapter(operation.kind).permission)
    return _xlsx(imports.correction(operation), f"c4rsys-{operation.kind}-correction.xlsx")


def _operation_for(
    request: HttpRequest, cmp: str, operation_id: UUID, permission: str
) -> tuple[companies.CompanyContext, Actor]:
    context, actor = _actor(request, cmp)
    operation = _operation(context.company.id, operation_id)
    _require(actor, permission, imports.adapter(operation.kind).permission)
    return context, actor


@require_POST
def import_reupload(request: HttpRequest, cmp: str, operation_id: UUID) -> JsonResponse:
    context, actor = _operation_for(request, cmp, operation_id, "import.run")
    raw_version = request.POST.get("expected_version") or "0"
    if not (raw_version.isascii() and raw_version.isdigit()):
        raise errors.validation_error("expected_version")
    expected_version = int(raw_version)
    content, name = _uploaded(request)
    operation = _operation(context.company.id, operation_id)
    imports.parse(content, operation.kind, correction=True)

    def handler(ctx: CommandContext) -> CommandResult:
        found = imports.reupload(
            ctx, context.company, operation_id, content, name, expected_version
        )
        return CommandResult(200, {"import": imports.payload(found)})

    payload = {
        "import": str(operation_id),
        "expected_version": expected_version,
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    return run_and_respond(request, "warehouse.import_reupload", actor, payload, handler)


@require_POST
def import_decisions(request: HttpRequest, cmp: str, operation_id: UUID) -> JsonResponse:
    context, actor = _operation_for(request, cmp, operation_id, "import.run")
    data = parse_json(request)
    expected_version = get_int(data, "expected_version")
    raw = data.get("decisions")
    if not isinstance(raw, list):
        raise errors.validation_error("decisions")
    decisions: list[tuple[str, str, str | None]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise errors.validation_error("decisions")
        decisions.append(
            (
                get_str(entry, "row", max_length=10),
                get_str(entry, "action", max_length=20),
                get_str(entry, "target", max_length=40, required=False) or None,
            )
        )

    def handler(ctx: CommandContext) -> CommandResult:
        operation = imports.decide(
            ctx, context.company.id, operation_id, decisions, expected_version
        )
        return CommandResult(200, {"import": imports.payload(operation)})

    payload = {
        "import": str(operation_id),
        "expected_version": expected_version,
        "decisions": [list(d) for d in decisions],
    }
    return run_and_respond(request, "warehouse.import_decide", actor, payload, handler)


@require_POST
def import_post(request: HttpRequest, cmp: str, operation_id: UUID) -> JsonResponse:
    context, actor = _operation_for(request, cmp, operation_id, "import.run")
    expected_version = get_int(parse_json(request), "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        operation = imports.post(ctx, context.company, operation_id, expected_version)
        return CommandResult(200, {"import": imports.payload(operation)})

    payload = {"import": str(operation_id), "expected_version": expected_version}
    return run_and_respond(request, "warehouse.import_post", actor, payload, handler)


@require_POST
def import_cancel(request: HttpRequest, cmp: str, operation_id: UUID) -> JsonResponse:
    """Cancel of a posted import is a separate right (§ 22.5.1)."""
    context, actor = _operation_for(request, cmp, operation_id, "import.cancel")
    expected_version = get_int(parse_json(request), "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        operation = imports.cancel(ctx, context.company.id, operation_id, expected_version)
        return CommandResult(200, {"import": imports.payload(operation)})

    payload = {"import": str(operation_id), "expected_version": expected_version}
    return run_and_respond(request, "warehouse.import_cancel", actor, payload, handler)
