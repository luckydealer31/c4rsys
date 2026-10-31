"""The personal garage of the cabinet: a vehicle of the confirmed owner (ТЗ § 7.1.2, § 8.2.3,
§ 8.2.7, § 8.3.6–8.3.9, § 8.3.15, § 9.1.4). The data come from the fleet module."""

from datetime import date
from typing import Any
from uuid import UUID

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_GET, require_POST

from c4rsys.accounts.middleware import require_user
from c4rsys.companies.views import run_and_respond
from c4rsys.fleet import lifecycle, owner_view, vehicle_documents
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import get_str, parse_json


def _date(value: Any, key: str) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise errors.validation_error(key, code="DATE_INVALID") from exc


def _uuid(value: Any, key: str) -> UUID | None:
    if value in (None, ""):
        return None
    try:
        return UUID(str(value))
    except ValueError as exc:
        raise errors.validation_error(key) from exc


def _file(file_id: UUID) -> HttpResponse:
    from c4rsys.files import services as files
    from c4rsys.files.models import StoredFile

    item = StoredFile.objects.filter(pk=file_id, state__in=files.AVAILABLE).first()
    if item is None:
        raise errors.not_found()
    return files.file_response(item)


@require_GET
def vehicle(request: HttpRequest, vehicle_id: str) -> JsonResponse:
    user, _session = require_user(request)
    return JsonResponse({"vehicle": owner_view.detail(user.id, vehicle_id.upper())})


@require_POST
def add_document(request: HttpRequest, vehicle_id: str) -> JsonResponse:
    """A new global document or a new version of one (§ 8.2.3)."""
    user, session = require_user(request)
    entity = owner_view.vehicle_of(user.id, vehicle_id.upper())
    data = parse_json(request)
    document_id = _uuid(data.get("document_id"), "document_id")
    doc_type = get_str(data, "doc_type", max_length=20, required=document_id is None)
    title = get_str(data, "title", max_length=300, required=False)
    details = get_str(data, "details", max_length=1000, required=False)
    issued_on = _date(data.get("issued_on"), "issued_on")
    valid_until = _date(data.get("valid_until"), "valid_until")
    file_id = _uuid(data.get("file_id"), "file_id")

    def handler(ctx: CommandContext) -> CommandResult:
        vehicle_documents.add_version(
            ctx,
            entity.id,
            owner_user_id=user.id,
            owner_company_id=None,
            document_id=document_id,
            doc_type=doc_type,
            title=title,
            details=details,
            issued_on=issued_on,
            valid_until=valid_until,
            file_id=file_id,
        )
        return CommandResult(201, {"vehicle": owner_view.detail(user.id, entity.public_id)})

    payload = {
        "vehicle": entity.public_id,
        "document_id": str(document_id) if document_id else None,
        "doc_type": doc_type,
        "title": title,
        "details": details,
        "issued_on": issued_on.isoformat() if issued_on else None,
        "valid_until": valid_until.isoformat() if valid_until else None,
        "file_id": str(file_id) if file_id else None,
    }
    actor = Actor(user_id=user.id, session_id=session.id)
    return run_and_respond(request, "cabinet.vehicle_document", actor, payload, handler)


@require_GET
def document_file(request: HttpRequest, vehicle_id: str, version_id: UUID) -> HttpResponse:
    user, _session = require_user(request)
    entity = owner_view.vehicle_of(user.id, vehicle_id.upper())
    return _file(vehicle_documents.owner_version_file(entity.id, user.id, version_id))


@require_GET
def copy_file(
    request: HttpRequest, vehicle_id: str, card_id: UUID, version_id: UUID
) -> HttpResponse:
    user, _session = require_user(request)
    entity = owner_view.vehicle_of(user.id, vehicle_id.upper())
    return _file(vehicle_documents.copy_file(entity.id, user.id, card_id, version_id))


@require_POST
def copy_decide(
    request: HttpRequest, vehicle_id: str, card_id: UUID, version_id: UUID
) -> JsonResponse:
    """§ 8.2.7: a company's copy accepted as a new version, or kept in that company only."""
    user, session = require_user(request)
    entity = owner_view.vehicle_of(user.id, vehicle_id.upper())
    data = parse_json(request)
    decision = get_str(data, "decision", max_length=15)
    doc_type = get_str(data, "doc_type", max_length=20, required=False)
    document_id = _uuid(data.get("document_id"), "document_id")

    def handler(ctx: CommandContext) -> CommandResult:
        vehicle_documents.decide_copy(
            ctx,
            entity.id,
            owner_user_id=user.id,
            owner_company_id=None,
            card_id=card_id,
            version_id=version_id,
            decision=decision,
            doc_type=doc_type,
            document_id=document_id,
        )
        return CommandResult(200, {"vehicle": owner_view.detail(user.id, entity.public_id)})

    payload = {
        "vehicle": entity.public_id,
        "card_id": str(card_id),
        "version_id": str(version_id),
        "decision": decision,
        "doc_type": doc_type,
        "document_id": str(document_id) if document_id else None,
    }
    actor = Actor(user_id=user.id, session_id=session.id)
    return run_and_respond(request, "cabinet.vehicle_copy_decide", actor, payload, handler)


@require_GET
def reversals(request: HttpRequest) -> JsonResponse:
    user, _session = require_user(request)
    return JsonResponse({"reversals": owner_view.open_reversals(user.id)})


@require_POST
def reversal_answer(request: HttpRequest, reversal_id: UUID) -> JsonResponse:
    """The new owner confirms or refuses that the transfer was wrong (§ 8.3.9)."""
    user, session = require_user(request)
    data = parse_json(request)
    decision = get_str(data, "decision", max_length=10)
    if decision not in ("accept", "reject"):
        raise errors.validation_error("decision", code="CHOICE_REQUIRED")
    comment = get_str(data, "comment", max_length=2000, required=False)

    def handler(ctx: CommandContext) -> CommandResult:
        found = lifecycle.answer_reversal(ctx, user.id, reversal_id, decision == "accept", comment)
        return CommandResult(200, {"reversal": lifecycle.reversal_payload(found)})

    payload = {"reversal_id": str(reversal_id), "decision": decision, "comment": comment}
    actor = Actor(user_id=user.id, session_id=session.id)
    return run_and_respond(request, "cabinet.transfer_reversal_answer", actor, payload, handler)
