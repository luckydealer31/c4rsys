"""HTTP API of the documents registry (ТЗ § 18.1, § 18.5, § 23.8.1; decisions Р-5…Р-11).

Viewing and every action need the right on documents in the document's scope; a link from a
card never opens a document by itself (§ 18.5.3).
"""

from datetime import date
from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from c4rsys.accounts.middleware import require_user
from c4rsys.companies import access
from c4rsys.companies import services as companies
from c4rsys.companies.views import run_and_respond
from c4rsys.documents import services
from c4rsys.documents.models import Document, DocumentLink
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import get_int, get_str, parse_json

LIST_LIMIT = 300


def _actor(request: HttpRequest, cmp: str) -> tuple[companies.CompanyContext, Actor]:
    user, session = require_user(request)
    context = companies.resolve_company(user, cmp, session.id)
    return context, companies.company_actor(user, context, session.id)


def _anywhere(actor: Actor, permission: str) -> bool:
    return access.RightsResolver().has_permission(actor, permission)


def _require_on(actor: Actor, permission: str, d: Document) -> None:
    if not services.can(actor, permission, d):
        raise errors.permission_denied(permission)


def _uuid(value: Any, key: str) -> UUID | None:
    if value in (None, ""):
        return None
    try:
        return UUID(str(value))
    except ValueError as exc:
        raise errors.validation_error(key) from exc


def _required(data: dict[str, Any], key: str) -> UUID:
    value = _uuid(data.get(key), key)
    if value is None:
        raise errors.validation_error(key, code="FIELD_REQUIRED")
    return value


def _date(data: dict[str, Any], key: str) -> date | None:
    value = data.get(key)
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise errors.validation_error(key, code="DATE_INVALID") from exc


def _days(data: dict[str, Any], key: str) -> int | None:
    value = data.get(key)
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise errors.validation_error(key)
    try:
        number = int(str(value))
    except ValueError as exc:
        raise errors.validation_error(key) from exc
    if number < 0 or number > 3650:
        raise errors.validation_error(key)
    return number


def _links(data: dict[str, Any]) -> list[services.Link]:
    value = data.get("links") or []
    if not isinstance(value, list):
        raise errors.validation_error("links")
    result = []
    for item in value:
        if not isinstance(item, dict):
            raise errors.validation_error("links")
        result.append(
            services.Link(
                object_type=get_str(item, "object_type", max_length=20),
                object_id=_required(item, "object_id"),
            )
        )
    return result


def _scope(data: dict[str, Any]) -> services.Scope | None:
    """`locations`: absent — by default; "all" — the whole company; a list — those."""
    if "locations" not in data or data["locations"] is None:
        return None
    value = data["locations"]
    if value == "all":
        return services.Scope(frozenset())
    if not isinstance(value, list) or not value:
        raise errors.validation_error("locations")
    return services.Scope(frozenset(_required({"l": x}, "l") for x in value))


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _kind_payload(kind: Any) -> dict[str, Any]:
    return {"id": str(kind.id), "code": kind.code or None, "name": kind.name or None}


def _document_payload(d: Document, *, detail: bool = False) -> dict[str, Any]:
    today = timezone.localdate()
    current = services.current_version(d)
    result: dict[str, Any] = {
        "id": str(d.id),
        "version": d.version,
        "number": d.number,
        "title": d.title,
        "kind_id": str(d.kind_id),
        "document_date": d.document_date.isoformat(),
        "all_locations": d.all_locations,
        "locations": sorted(str(x) for x in services.location_ids(d)),
        "valid_until": _iso(d.valid_until),
        "warn_days": d.warn_days,
        "warning": services.warning(d, today),
        "state": d.state,
        "archive_reason": d.archive_reason,
        "current_version": {
            "number": current.number,
            "file_id": str(current.file_id) if current.file_id else None,
            "file_name": current.file_name,
            "has_content": bool(current.content),
        }
        if current
        else None,
    }
    if detail:
        result["links"] = [
            {"id": str(x.id), "object_type": x.object_type, "object_id": str(x.object_id)}
            for x in services.links_of(d)
        ]
        result["versions"] = [
            {
                "number": v.number,
                "file_id": str(v.file_id) if v.file_id else None,
                "file_name": v.file_name,
                "valid_until": _iso(v.valid_until),
                "note": v.note,
                "created_at": v.created_at.isoformat(),
            }
            for v in services.versions_of(d)
        ]
    return result


@require_http_methods(["GET", "POST"])
def kind_list(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        if not any(_anywhere(actor, p) for p in ("documents.view", "documents.upload")):
            raise errors.permission_denied("documents.view")
        kinds = services.kinds_of(context.company.id)
        return JsonResponse({"kinds": [_kind_payload(k) for k in kinds]})
    if not _anywhere(actor, "documents.templates"):
        raise errors.permission_denied("documents.templates")
    name = get_str(parse_json(request), "name", max_length=200).strip()

    def handler(ctx: CommandContext) -> CommandResult:
        return CommandResult(
            201, {"kind": _kind_payload(services.add_kind(ctx, context.company, name))}
        )

    return run_and_respond(request, "document_kind.create", actor, {"name": name}, handler)


@require_http_methods(["GET", "POST"])
def document_list(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        params = request.GET
        query = services.documents_of(context.company.id)
        if params.get("object_type") and params.get("object_id"):
            object_id = _required({"object_id": params["object_id"]}, "object_id")
            query = services.linked_to(context.company.id, params["object_type"], object_id)
        if params.get("archived") != "1":
            query = query.filter(state=Document.State.ACTIVE)
        if params.get("kind_id"):
            query = query.filter(kind_id=_uuid(params["kind_id"], "kind_id"))
        items = [
            d
            for d in query.order_by("-number")[:LIST_LIMIT]
            if services.can(actor, "documents.view", d)
        ]
        if params.get("warnings") == "1":
            today = timezone.localdate()
            items = [d for d in items if services.warning(d, today) is not None]
        return JsonResponse({"documents": [_document_payload(d) for d in items]})

    data = parse_json(request)
    new = services.NewDocument(
        title=get_str(data, "title", max_length=300).strip(),
        kind_id=_required(data, "kind_id"),
        document_date=_date(data, "document_date") or timezone.localdate(),
        file_id=_required(data, "file_id"),
        file_name=get_str(data, "file_name", max_length=255, required=False),
        links=_links(data),
        scope=_scope(data) or services.Scope(None),
        valid_until=_date(data, "valid_until"),
        warn_days=_days(data, "warn_days"),
    )
    if not _anywhere(actor, "documents.upload"):
        raise errors.permission_denied("documents.upload")

    def handler(ctx: CommandContext) -> CommandResult:
        d = services.create(ctx, context.company, new)
        _require_on(actor, "documents.upload", d)
        return CommandResult(201, {"document": _document_payload(d, detail=True)})

    payload = {
        k: data.get(k)
        for k in (
            "title",
            "kind_id",
            "document_date",
            "file_id",
            "links",
            "locations",
            "valid_until",
            "warn_days",
        )
    }
    return run_and_respond(request, "document.create", actor, payload, handler)


def _document_for(context: companies.CompanyContext, actor: Actor, document_id: UUID) -> Document:
    d = services.documents_of(context.company.id).filter(pk=document_id).first()
    if d is None or not services.can(actor, "documents.view", d):
        raise errors.not_found()
    return d


@require_http_methods(["GET", "PATCH"])
def document_detail(request: HttpRequest, cmp: str, document_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    d = _document_for(context, actor, document_id)
    if request.method == "GET":
        return JsonResponse({"document": _document_payload(d, detail=True)})
    _require_on(actor, "documents.upload", d)
    data = parse_json(request)
    changes = services.Changes(
        title=get_str(data, "title", max_length=300, required=False).strip() or None,
        kind_id=_uuid(data.get("kind_id"), "kind_id"),
        document_date=_date(data, "document_date"),
        scope=_scope(data),
        warn_days=_days(data, "warn_days"),
        warn_days_set="warn_days" in data,
    )
    expected_version = get_int(data, "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        locked = services.locked(context.company.id, document_id, expected_version)
        services.change(ctx, locked, changes)
        return CommandResult(200, {"document": _document_payload(locked, detail=True)})

    payload = {
        "document_id": str(document_id),
        **{
            k: data.get(k)
            for k in (
                "title",
                "kind_id",
                "document_date",
                "locations",
                "warn_days",
                "expected_version",
            )
        },
    }
    return run_and_respond(request, "document.change", actor, payload, handler)


def _command(
    request: HttpRequest,
    cmp: str,
    document_id: UUID,
    permission: str,
    command: str,
    action: Any,
    keys: tuple[str, ...],
) -> JsonResponse:
    context, actor = _actor(request, cmp)
    d = _document_for(context, actor, document_id)
    _require_on(actor, permission, d)
    data = parse_json(request)
    expected_version = get_int(data, "expected_version")
    action(None, None, data)

    def handler(ctx: CommandContext) -> CommandResult:
        locked = services.locked(context.company.id, document_id, expected_version)
        action(ctx, locked, data)
        locked.refresh_from_db()
        return CommandResult(200, {"document": _document_payload(locked, detail=True)})

    payload = {
        "document_id": str(document_id),
        "expected_version": expected_version,
        **{k: data.get(k) for k in keys},
    }
    return run_and_respond(request, command, actor, payload, handler)


@require_POST
def document_version(request: HttpRequest, cmp: str, document_id: UUID) -> JsonResponse:
    """A replacement or an extension as a new version (§ 18.1.14)."""

    def action(ctx: CommandContext | None, d: Any, data: dict[str, Any]) -> None:
        file_id = _required(data, "file_id")
        valid_until = _date(data, "valid_until")
        note = get_str(data, "note", max_length=1000, required=False).strip()
        name = get_str(data, "file_name", max_length=255, required=False)
        if ctx is not None:
            services.add_version(
                ctx, d, file_id=file_id, file_name=name, valid_until=valid_until, note=note
            )

    return _command(
        request,
        cmp,
        document_id,
        "documents.upload",
        "document.version",
        action,
        ("file_id", "valid_until", "note"),
    )


@require_POST
def document_link(request: HttpRequest, cmp: str, document_id: UUID) -> JsonResponse:
    def action(ctx: CommandContext | None, d: Any, data: dict[str, Any]) -> None:
        link = services.Link(
            object_type=get_str(data, "object_type", max_length=20),
            object_id=_required(data, "object_id"),
        )
        if link.object_type not in DocumentLink.ObjectType.values:
            raise errors.validation_error("object_type")
        if ctx is not None:
            services.add_link(ctx, d, link)

    return _command(
        request,
        cmp,
        document_id,
        "documents.upload",
        "document.link",
        action,
        ("object_type", "object_id"),
    )


@require_POST
def document_unlink(request: HttpRequest, cmp: str, document_id: UUID) -> JsonResponse:
    def action(ctx: CommandContext | None, d: Any, data: dict[str, Any]) -> None:
        link_id = _required(data, "link_id")
        if ctx is not None:
            services.remove_link(ctx, d, link_id)

    return _command(
        request, cmp, document_id, "documents.upload", "document.unlink", action, ("link_id",)
    )


@require_POST
def document_archive(request: HttpRequest, cmp: str, document_id: UUID) -> JsonResponse:
    def action(ctx: CommandContext | None, d: Any, data: dict[str, Any]) -> None:
        reason = get_str(data, "reason", max_length=1000, required=False).strip()
        if not reason:
            raise errors.validation_error("reason", code="FIELD_REQUIRED")
        if ctx is not None:
            services.archive(ctx, d, reason)

    return _command(
        request, cmp, document_id, "documents.archive", "document.archive", action, ("reason",)
    )
