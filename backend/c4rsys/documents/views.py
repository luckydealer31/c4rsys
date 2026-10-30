"""HTTP API of the documents registry (ТЗ § 18.1, § 18.5, § 23.8.1; decisions Р-5…Р-11).

Viewing and every action need the right on documents in the document's scope; a link from a
card never opens a document by itself (§ 18.5.3).
"""

from datetime import date
from typing import Any
from uuid import UUID

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from c4rsys.accounts.middleware import require_user
from c4rsys.companies import access
from c4rsys.companies import services as companies
from c4rsys.companies.models import Company
from c4rsys.companies.views import run_and_respond
from c4rsys.documents import fields, inspection_act, requirements, services, signing, templates
from c4rsys.documents.models import Document, DocumentLink, DocumentTemplate
from c4rsys.files import services as files
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import client_ip, get_int, get_str, parse_json

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


def _status_payload(d: Document) -> dict[str, Any]:
    found = signing.status(d)
    return {"state": found.state, "version": found.version}


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
        "show_to_client": d.show_to_client,
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
        "template_id": str(d.template_id) if d.template_id else None,
        "template_version": d.template_version,
        "signing": _status_payload(d),
        "source_type": d.source_type or None,
        "source_id": str(d.source_id) if d.source_id else None,
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
                "content": v.content,
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


@require_POST
def document_client_visibility(request: HttpRequest, cmp: str, document_id: UUID) -> JsonResponse:
    """«Show to the client» (decision Б-2); the same right as changing the document."""

    def action(ctx: CommandContext | None, d: Any, data: dict[str, Any]) -> None:
        visible = data.get("visible")
        if not isinstance(visible, bool):
            raise errors.validation_error("visible")
        if ctx is not None:
            services.set_client_visible(ctx, d, visible)

    return _command(
        request,
        cmp,
        document_id,
        "documents.upload",
        "document.client_visibility",
        action,
        ("visible",),
    )


# --- Templates (§ 18.1.4–18.1.5, § 18.2) ---------------------------------------------------------


def _template_payload(t: DocumentTemplate) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "version": t.version,
        "system": t.company_id is None,
        "code": t.code or None,
        "content": t.content,
        "kind_id": str(t.kind_id),
        **templates.snapshot(t),
    }


@require_http_methods(["GET"])
def field_catalog(request: HttpRequest, cmp: str) -> JsonResponse:
    """The agreed system fields and tables with the sources that give them (§ 18.2.6)."""
    _actor(request, cmp)
    return JsonResponse(
        {
            "fields": [{"code": k, "source": v or None} for k, v in fields.FIELDS.items()],
            "tables": [
                {"code": k, "columns": list(v), "source": templates.TABLE_SOURCES[k]}
                for k, v in fields.TABLES.items()
            ],
            "sources": {k: sorted(v) for k, v in templates.SOURCES_OF.items()},
        }
    )


def _template_data(data: dict[str, Any]) -> templates.TemplateData:
    required = data.get("required_fields") or []
    signers = data.get("signers") or {}
    if not isinstance(required, list) or not all(isinstance(x, str) for x in required):
        raise errors.validation_error("required_fields")
    if not isinstance(signers, dict) or not all(isinstance(v, bool) for v in signers.values()):
        raise errors.validation_error("signers")
    return templates.TemplateData(
        title=get_str(data, "title", max_length=300).strip(),
        kind_id=_required(data, "kind_id"),
        source=get_str(data, "source", max_length=20),
        content=get_str(data, "content", max_length=100_000),
        required_fields=required,
        signers={k: bool(v) for k, v in signers.items()},
        language=get_str(data, "language", max_length=5, required=False),
    )


@require_http_methods(["GET", "POST"])
def template_list(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        if not any(
            _anywhere(actor, p)
            for p in ("documents.view", "documents.create", "documents.templates")
        ):
            raise errors.permission_denied("documents.create")
        items = templates.templates_of(context.company.id).order_by(
            "company_id", "code", "language", "title"
        )
        return JsonResponse({"templates": [_template_payload(t) for t in items]})
    if not _anywhere(actor, "documents.templates"):
        raise errors.permission_denied("documents.templates")
    data = parse_json(request)
    copy_from = _uuid(data.get("copy_from"), "copy_from")
    if copy_from is not None:
        source = templates.get(context.company.id, copy_from)
        data = {
            "title": data.get("title") or source.title,
            "kind_id": str(source.kind_id),
            "source": source.source,
            "content": source.content,
            "required_fields": list(source.required_fields),
            "signers": dict(source.signers),
            "language": source.language,
        }
    new = _template_data(data)

    def handler(ctx: CommandContext) -> CommandResult:
        return CommandResult(
            201, {"template": _template_payload(templates.create(ctx, context.company, new))}
        )

    payload = {
        k: data.get(k)
        for k in ("title", "kind_id", "source", "content", "required_fields", "signers", "language")
    }
    payload["copy_from"] = str(copy_from) if copy_from else None
    return run_and_respond(request, "document_template.create", actor, payload, handler)


@require_http_methods(["GET", "PATCH"])
def template_detail(request: HttpRequest, cmp: str, template_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        if not any(
            _anywhere(actor, p)
            for p in ("documents.view", "documents.create", "documents.templates")
        ):
            raise errors.permission_denied("documents.create")
        return JsonResponse(
            {"template": _template_payload(templates.get(context.company.id, template_id))}
        )
    if not _anywhere(actor, "documents.templates"):
        raise errors.permission_denied("documents.templates")
    data = parse_json(request)
    expected_version = get_int(data, "expected_version")
    archived = data.get("archived")
    if archived is not None and not isinstance(archived, bool):
        raise errors.validation_error("archived")
    new = None if archived is not None else _template_data(data)

    def handler(ctx: CommandContext) -> CommandResult:
        t = templates.locked(context.company.id, template_id, expected_version)
        if new is not None:
            templates.change(ctx, t, new)
        else:
            templates.set_archived(ctx, t, bool(archived))
        return CommandResult(200, {"template": _template_payload(t)})

    payload = {
        "template_id": str(template_id),
        "expected_version": expected_version,
        "archived": archived,
        **{
            k: data.get(k)
            for k in (
                "title",
                "kind_id",
                "source",
                "content",
                "required_fields",
                "signers",
                "language",
            )
        },
    }
    return run_and_respond(request, "document_template.change", actor, payload, handler)


@require_POST
def document_preview(request: HttpRequest, cmp: str) -> JsonResponse:
    """The result before making, with the fields without data (§ 18.2.4); nothing is saved."""
    context, actor = _actor(request, cmp)
    if not _anywhere(actor, "documents.create"):
        raise errors.permission_denied("documents.create")
    data = parse_json(request)
    t = templates.get(context.company.id, _required(data, "template_id"))
    found = templates.preview(
        context.company,
        t,
        _uuid(data.get("source_id"), "source_id"),
        employment_id=actor.employment_id,
        on=_date(data, "document_date"),
    )
    return JsonResponse(
        {
            "html": found.html,
            "missing_required": found.missing_required,
            "missing_optional": found.missing_optional,
        }
    )


@require_POST
def document_from_template(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if not _anywhere(actor, "documents.create"):
        raise errors.permission_denied("documents.create")
    data = parse_json(request)
    template_id = _required(data, "template_id")
    source_id = _uuid(data.get("source_id"), "source_id")
    title = get_str(data, "title", max_length=300, required=False).strip()
    on = _date(data, "document_date") or timezone.localdate()

    def handler(ctx: CommandContext) -> CommandResult:
        t = templates.get(context.company.id, template_id)
        d = templates.make(ctx, context.company, t, source_id, title=title, on=on)
        _require_on(actor, "documents.create", d)
        return CommandResult(201, {"document": _document_payload(d, detail=True)})

    payload = {
        "template_id": str(template_id),
        "source_id": str(source_id) if source_id else None,
        "title": title,
        "document_date": on.isoformat(),
    }
    return run_and_respond(request, "document.from_template", actor, payload, handler)


@require_POST
def document_remake(request: HttpRequest, cmp: str, document_id: UUID) -> JsonResponse:
    """An unsigned document made again by the current template (§ 18.5.2)."""
    context, actor = _actor(request, cmp)

    def action(ctx: CommandContext | None, d: Any, data: dict[str, Any]) -> None:
        if ctx is not None:
            templates.remake(ctx, context.company, d)

    return _command(request, cmp, document_id, "documents.create", "document.remake", action, ())


# --- Signing (§ 18.3, § 18.4.2) ------------------------------------------------------------------


def _signer_payload(d: Document, x: Any, actor: Actor) -> dict[str, Any]:
    invitation = signing.active_invitation(x)
    return {
        "id": str(x.id),
        "version_number": x.document_version.number,
        "role": x.role,
        "name": x.name,
        "employment_id": str(x.employment_id) if x.employment_id else None,
        "client_id": str(x.client_id) if x.client_id else None,
        "counterparty_id": str(x.counterparty_id) if x.counterparty_id else None,
        "is_me": x.employment_id is not None and x.employment_id == actor.employment_id,
        "state": x.state,
        "method": x.method or None,
        "decided_at": _iso(x.decided_at),
        "confirmed_contact": x.confirmed_contact or None,
        "comment": x.comment,
        "paper_file_id": str(x.paper_file_id) if x.paper_file_id else None,
        "paper_signed_on": _iso(x.paper_signed_on),
        "entered_at": _iso(x.entered_at),
        "signature_file_id": str(x.signature_file_id) if x.signature_file_id else None,
        "presented_by_me": x.method == signing.Method.ON_SCREEN
        and x.entered_by_user_id == actor.user_id,
        "emails": signing.emails_of(d, x) if x.state == "pending" else [],
        "link": {"email": invitation.email, "expires_at": invitation.expires_at.isoformat()}
        if invitation is not None
        else None,
    }


def _signing_payload(d: Document, actor: Actor) -> dict[str, Any]:
    sent = signing.sent_version(d)
    current = services.current_version(d)
    roles = d.template.signers if d.template is not None else {}
    return {
        "document_version": d.version,
        "status": _status_payload(d),
        "methods": signing.methods_of(d.company_id, d.kind_id),
        "on_screen": signing.on_screen_allowed(d),
        "current_version": current.number if current else None,
        "sent_version": sent.number if sent else None,
        "template_signers": sorted(role for role, needed in roles.items() if needed),
        "signers": [_signer_payload(d, x, actor) for x in signing.signers_of(d)],
        "candidates": [
            {"role": "company", "id": str(x), "name": name}
            for x, name in signing.company_candidates(d)
        ]
        + [
            {"role": role, "id": str(x), "name": name}
            for role, x, name in signing.party_candidates(d)
        ],
    }


def _people(data: dict[str, Any]) -> list[signing.Person]:
    value = data.get("signers")
    if not isinstance(value, list) or not value or len(value) > 20:
        raise errors.validation_error("signers", code="FIELD_REQUIRED")
    result = []
    for item in value:
        if not isinstance(item, dict) or item.get("role") not in signing.Role.values:
            raise errors.validation_error("signers")
        result.append(signing.Person(role=item["role"], person_id=_required(item, "id")))
    return result


@require_http_methods(["GET", "POST"])
def document_signing(request: HttpRequest, cmp: str, document_id: UUID) -> JsonResponse:
    """GET — signers of every sent version; POST — send the current version (§ 18.3.5)."""
    context, actor = _actor(request, cmp)
    d = _document_for(context, actor, document_id)
    if request.method == "GET":
        return JsonResponse({"signing": _signing_payload(d, actor)})
    _require_on(actor, "documents.upload", d)
    data = parse_json(request)
    expected_version = get_int(data, "expected_version")
    people = _people(data)

    def handler(ctx: CommandContext) -> CommandResult:
        locked = services.locked(context.company.id, document_id, expected_version)
        signing.send(ctx, locked, people)
        return CommandResult(200, {"signing": _signing_payload(locked, actor)})

    payload = {
        "document_id": str(document_id),
        "expected_version": expected_version,
        "signers": data.get("signers"),
    }
    return run_and_respond(request, "document.send_for_signing", actor, payload, handler)


def _signer_command(
    request: HttpRequest,
    cmp: str,
    document_id: UUID,
    signer_id: UUID,
    permission: str,
    command: str,
    action: Any,
    keys: tuple[str, ...],
) -> JsonResponse:
    """`action(ctx, d, signer, data)` may return an error to answer with after the commit."""
    context, actor = _actor(request, cmp)
    d = _document_for(context, actor, document_id)
    _require_on(actor, permission, d)
    data = parse_json(request)
    action(None, None, None, data)

    def handler(ctx: CommandContext) -> CommandResult:
        locked = services.locked(context.company.id, document_id, None)
        signer = signing.signer_of(locked, signer_id)
        failed = action(ctx, locked, signer, data)
        if failed is not None:
            return CommandResult(failed.status, failed.payload(ctx.request_id))
        return CommandResult(200, {"signing": _signing_payload(locked, actor)})

    payload = {
        "document_id": str(document_id),
        "signer_id": str(signer_id),
        **{k: data.get(k) for k in keys},
    }
    return run_and_respond(request, command, actor, payload, handler)


@require_POST
def signer_code(request: HttpRequest, cmp: str, document_id: UUID, signer_id: UUID) -> JsonResponse:
    """A one-time code to the employee's email before signing for the company (§ 18.3.9)."""

    def action(ctx: CommandContext | None, d: Any, signer: Any, data: dict[str, Any]) -> None:
        if ctx is not None:
            signing.request_code(ctx, d, signer)

    return _signer_command(
        request, cmp, document_id, signer_id, "documents.sign", "document.signing_code", action, ()
    )


def _decision(data: dict[str, Any]) -> tuple[str, str]:
    decision = get_str(data, "decision", max_length=10)
    if decision not in ("sign", "decline"):
        raise errors.validation_error("decision")
    comment = get_str(data, "comment", max_length=2000, required=False).strip()
    return decision, comment


@require_POST
def signer_decide(
    request: HttpRequest, cmp: str, document_id: UUID, signer_id: UUID
) -> JsonResponse:
    """The company's signer signs or declines with the code (§ 18.3.3–18.3.4)."""

    def action(ctx: CommandContext | None, d: Any, signer: Any, data: dict[str, Any]) -> Any:
        code = get_str(data, "code", max_length=10)
        decision, comment = _decision(data)
        if ctx is not None:
            return signing.decide_in_app(ctx, d, signer, code, decision, comment)
        return None

    return _signer_command(
        request,
        cmp,
        document_id,
        signer_id,
        "documents.sign",
        "document.signing_decide",
        action,
        ("code", "decision", "comment"),
    )


@require_POST
def signer_invite(
    request: HttpRequest, cmp: str, document_id: UUID, signer_id: UUID
) -> JsonResponse:
    """A personal link to the client or the counterparty (§ 18.3.8–18.3.9)."""

    def action(ctx: CommandContext | None, d: Any, signer: Any, data: dict[str, Any]) -> None:
        email = get_str(data, "email", max_length=254)
        days = _days(data, "days")
        if ctx is not None:
            signing.invite(ctx, d, signer, email, days)

    return _signer_command(
        request,
        cmp,
        document_id,
        signer_id,
        "documents.upload",
        "document.signing_invite",
        action,
        ("email", "days"),
    )


@require_POST
def signer_revoke(
    request: HttpRequest, cmp: str, document_id: UUID, signer_id: UUID
) -> JsonResponse:
    def action(ctx: CommandContext | None, d: Any, signer: Any, data: dict[str, Any]) -> None:
        if ctx is not None:
            signing.revoke(ctx, d, signer)

    return _signer_command(
        request,
        cmp,
        document_id,
        signer_id,
        "documents.upload",
        "document.signing_revoke",
        action,
        (),
    )


@require_POST
def signer_paper(
    request: HttpRequest, cmp: str, document_id: UUID, signer_id: UUID
) -> JsonResponse:
    """A scan of the paper signature with the date of signing (§ 18.3.7)."""

    def action(ctx: CommandContext | None, d: Any, signer: Any, data: dict[str, Any]) -> None:
        file_id = _required(data, "file_id")
        signed_on = _date(data, "signed_on")
        if signed_on is None:
            raise errors.validation_error("signed_on", code="FIELD_REQUIRED")
        if ctx is not None:
            signing.paper(ctx, d, signer, file_id, signed_on)

    return _signer_command(
        request,
        cmp,
        document_id,
        signer_id,
        "documents.upload",
        "document.signing_paper",
        action,
        ("file_id", "signed_on"),
    )


@require_POST
def signer_on_screen(
    request: HttpRequest, cmp: str, document_id: UUID, signer_id: UUID
) -> JsonResponse:
    """The client answers on the screen of the employee's device (§ 10.2.2, § 18.4.2).
    TODO(spec): the right to present the act is not stated. PROPOSAL: «Upload documents», as for
    sending a personal link or entering a paper signature."""

    def action(ctx: CommandContext | None, d: Any, signer: Any, data: dict[str, Any]) -> None:
        decision, comment = _decision(data)
        version_number = get_int(data, "version_number")
        consent = data.get("consent", False)
        if not isinstance(consent, bool):
            raise errors.validation_error("consent")
        file_id = _uuid(data.get("file_id"), "file_id")
        if ctx is not None:
            signing.on_screen(
                ctx,
                d,
                signer,
                version_number=version_number,
                decision=decision,
                consent=consent,
                file_id=file_id,
                comment=comment,
            )

    return _signer_command(
        request,
        cmp,
        document_id,
        signer_id,
        "documents.upload",
        "document.signing_on_screen",
        action,
        ("version_number", "decision", "consent", "file_id", "comment"),
    )


@require_POST
def inspection_act_create(request: HttpRequest, cmp: str, inspection_id: UUID) -> JsonResponse:
    """The act of a saved inspection (decision В-8.3): «Create documents by templates» and the
    inspection seen by the employee (§ 18.1.8: its own rules of access stay)."""
    from c4rsys.inspections import access as inspection_access
    from c4rsys.inspections import services as inspections

    context, actor = _actor(request, cmp)
    if not _anywhere(actor, "documents.create"):
        raise errors.permission_denied("documents.create")
    inspection = inspections.get(context.company.id, inspection_id)
    if not inspection_access.can_view(actor, inspection):
        raise errors.not_found()
    data = parse_json(request)
    template_id = _uuid(data.get("template_id"), "template_id")
    language = get_str(data, "language", max_length=5, required=False).strip()

    def handler(ctx: CommandContext) -> CommandResult:
        d = inspection_act.make(
            ctx, context.company, inspection_id, template_id=template_id, language=language
        )
        _require_on(actor, "documents.create", d)
        return CommandResult(201, {"document": _document_payload(d, detail=True)})

    payload = {
        "inspection_id": str(inspection_id),
        "template_id": str(template_id) if template_id else None,
        "language": language,
    }
    return run_and_respond(request, "document.inspection_act", actor, payload, handler)


@require_http_methods(["GET", "POST"])
def kind_signing(request: HttpRequest, cmp: str) -> JsonResponse:
    """Allowed ways of signing by kind (§ 18.4.2): GET — all kinds; POST — one kind."""
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        if not any(_anywhere(actor, p) for p in ("documents.view", "documents.templates")):
            raise errors.permission_denied("documents.view")
        found = signing.all_methods(context.company.id)
        return JsonResponse(
            {
                "kinds": [
                    {
                        "kind_id": str(k.id),
                        "methods": found.get(k.id, ("both", True))[0],
                        "on_screen": found.get(k.id, ("both", True))[1],
                    }
                    for k in services.kinds_of(context.company.id)
                ]
            }
        )
    if not _anywhere(actor, "documents.templates"):
        raise errors.permission_denied("documents.templates")
    data = parse_json(request)
    kind_id = _required(data, "kind_id")
    methods = get_str(data, "methods", max_length=10)
    on_screen = data.get("on_screen")
    if on_screen is not None and not isinstance(on_screen, bool):
        raise errors.validation_error("on_screen")

    def handler(ctx: CommandContext) -> CommandResult:
        signing.set_methods(ctx, context.company, kind_id, methods, on_screen)
        return CommandResult(
            200,
            {
                "kind_id": str(kind_id),
                "methods": methods,
                "on_screen": signing.on_screen_of(context.company.id, kind_id),
            },
        )

    payload = {"kind_id": str(kind_id), "methods": methods, "on_screen": on_screen}
    return run_and_respond(request, "document_kind.signing", actor, payload, handler)


# --- The public page of a personal link (§ 18.3.10) ----------------------------------------------


def _link_payload(invitation: Any) -> dict[str, Any]:
    """Only the assigned version and its signer: no order, cards or other documents."""
    signer = invitation.signer
    version = signer.document_version
    d = signer.document
    company = Company.objects.filter(pk=d.company_id).values_list("display_name", flat=True).first()
    return {
        "company": company,
        "title": d.title,
        "number": d.number,
        "version_number": version.number,
        "content": version.content,
        "has_file": version.file_id is not None,
        "file_name": version.file_name,
        "signer_name": signer.name,
        "email": signing.masked(invitation.email),
        "expires_at": invitation.expires_at.isoformat(),
    }


def _require_key(request: HttpRequest) -> None:
    if not request.headers.get("Idempotency-Key", "").strip():
        raise errors.validation_error("Idempotency-Key", code="IDEMPOTENCY_KEY_REQUIRED")


@require_http_methods(["GET"])
def link_open(request: HttpRequest, token: str) -> JsonResponse:
    return JsonResponse({"link": _link_payload(signing.by_token(token))})


@require_http_methods(["GET"])
def link_file(request: HttpRequest, token: str) -> HttpResponse:
    invitation = signing.by_token(token)
    version = invitation.signer.document_version
    item = files.of_company(invitation.company_id, version.file_id) if version.file_id else None
    if item is None:
        raise errors.not_found()
    response = HttpResponse(files.content(item), content_type=item.content_type)
    response["Cache-Control"] = "private, no-store"
    response["Content-Disposition"] = "inline"
    return response


@require_POST
def link_code(request: HttpRequest, token: str) -> JsonResponse:
    """A one-time code to the email the link was sent to (§ 18.3.9)."""
    _require_key(request)
    invitation = signing.link_code(token, client_ip(request))
    return JsonResponse({"sent_to": signing.masked(invitation.email)})


@require_POST
def link_decide(request: HttpRequest, token: str) -> JsonResponse:
    _require_key(request)
    data = parse_json(request)
    code = get_str(data, "code", max_length=10)
    decision, comment = _decision(data)
    invitation = signing.link_decide(token, code, decision, comment)
    return JsonResponse({"state": invitation.signer.state})


# --- Required documents (§ 18.4.1, § 16.7.6; decisions Р-8, Р-9) ---------------------------------


def _requirement_payload(r: Any) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "stage": r.stage,
        "need": r.need,
        "template_id": str(r.template_id) if r.template_id else None,
        "template_title": r.template.title if r.template else None,
        "kind_id": str(r.kind_id) if r.kind_id else None,
    }


def _settings_reader(actor: Actor) -> None:
    if not any(_anywhere(actor, p) for p in ("company.settings", "documents.view")):
        raise errors.permission_denied("company.settings")


@require_http_methods(["GET", "POST"])
def requirement_list(request: HttpRequest, cmp: str) -> JsonResponse:
    """Documents required at the stages of rentals of the company."""
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        _settings_reader(actor)
        items = requirements.of_company(context.company.id)
        return JsonResponse({"requirements": [_requirement_payload(r) for r in items]})
    if not _anywhere(actor, "company.settings"):
        raise errors.permission_denied("company.settings")
    data = parse_json(request)
    stage = get_str(data, "stage", max_length=15)
    need = get_str(data, "need", max_length=10)
    template_id = _uuid(data.get("template_id"), "template_id")
    kind_id = _uuid(data.get("kind_id"), "kind_id")

    def handler(ctx: CommandContext) -> CommandResult:
        item = requirements.add(
            ctx, context.company, stage=stage, need=need, template_id=template_id, kind_id=kind_id
        )
        return CommandResult(201, {"requirement": _requirement_payload(item)})

    payload = {k: data.get(k) for k in ("stage", "need", "template_id", "kind_id")}
    return run_and_respond(request, "document_requirement.add", actor, payload, handler)


@require_POST
def requirement_remove(request: HttpRequest, cmp: str, requirement_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if not _anywhere(actor, "company.settings"):
        raise errors.permission_denied("company.settings")

    def handler(ctx: CommandContext) -> CommandResult:
        requirements.remove(ctx, context.company, requirement_id)
        return CommandResult(200, {"removed": str(requirement_id)})

    payload = {"requirement_id": str(requirement_id)}
    return run_and_respond(request, "document_requirement.remove", actor, payload, handler)


def _act_payload(setting: requirements.ActSetting) -> dict[str, Any]:
    return {
        "act_required": setting.act_required,
        "act_signed": setting.act_signed,
        "version": setting.version,
    }


@require_http_methods(["GET", "PUT"])
def service_act_setting(request: HttpRequest, cmp: str) -> JsonResponse:
    """The act of completed works of a client's service: required, signed by the client."""
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        if not any(_anywhere(actor, p) for p in ("company.settings", "service.view")):
            raise errors.permission_denied("company.settings")
        return JsonResponse({"setting": _act_payload(requirements.service_act(context.company.id))})
    if not _anywhere(actor, "company.settings"):
        raise errors.permission_denied("company.settings")
    data = parse_json(request)
    act_required = data.get("act_required")
    act_signed = data.get("act_signed")
    if not isinstance(act_required, bool) or not isinstance(act_signed, bool):
        raise errors.validation_error("act_required")
    expected = get_int(data, "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        setting = requirements.set_service_act(
            ctx, context.company, act_required, act_signed, expected
        )
        return CommandResult(200, {"setting": _act_payload(setting)})

    payload = {"act_required": act_required, "act_signed": act_signed, "expected": expected}
    return run_and_respond(request, "service.act_setting", actor, payload, handler)
