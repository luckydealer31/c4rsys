"""The personal garage of the cabinet: a vehicle of the confirmed owner (ТЗ § 7.1.2, § 8.2.3,
§ 8.2.7, § 8.3.6–8.3.9, § 8.3.15, § 9.1.4). The data come from the fleet module."""

from datetime import date
from typing import Any
from uuid import UUID

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_GET, require_POST

from c4rsys.accounts.middleware import require_user
from c4rsys.companies.views import run_and_respond
from c4rsys.fleet import document_transfer, lifecycle, owner_view, vehicle_documents
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
    """The owner's document of the vehicle, new or changed: the name, the series and number, the
    start and the end of the term, an optional JPEG or PDF file (decision of 12.11.2026). A
    change is kept as a new version (§ 8.2.3)."""
    user, session = require_user(request)
    entity = owner_view.vehicle_of(user.id, vehicle_id.upper())
    data = parse_json(request)
    document_id = _uuid(data.get("document_id"), "document_id")
    name = get_str(data, "name", max_length=300)
    number = get_str(data, "number", max_length=150, required=False)
    issued_by = get_str(data, "issued_by", max_length=200, required=False)
    valid_from = _date(data.get("valid_from"), "valid_from")
    valid_until = _date(data.get("valid_until"), "valid_until")
    file_id = _uuid(data.get("file_id"), "file_id")
    keep_file = data.get("keep_file") is True

    def handler(ctx: CommandContext) -> CommandResult:
        vehicle_documents.owner_save(
            ctx,
            entity.id,
            owner_user_id=user.id,
            owner_company_id=None,
            document_id=document_id,
            name=name,
            number=number,
            issued_by=issued_by,
            valid_from=valid_from,
            valid_until=valid_until,
            file_id=file_id,
            keep_file=keep_file,
        )
        return CommandResult(201, {"vehicle": owner_view.detail(user.id, entity.public_id)})

    payload = {
        "vehicle": entity.public_id,
        "document_id": str(document_id) if document_id else None,
        "name": name,
        "number": number,
        "issued_by": issued_by,
        "valid_from": valid_from.isoformat() if valid_from else None,
        "valid_until": valid_until.isoformat() if valid_until else None,
        "file_id": str(file_id) if file_id else None,
        "keep_file": keep_file,
    }
    actor = Actor(user_id=user.id, session_id=session.id)
    return run_and_respond(request, "cabinet.vehicle_document", actor, payload, handler)


@require_POST
def archive_document(request: HttpRequest, vehicle_id: str, document_id: UUID) -> JsonResponse:
    user, session = require_user(request)
    entity = owner_view.vehicle_of(user.id, vehicle_id.upper())

    def handler(ctx: CommandContext) -> CommandResult:
        vehicle_documents.owner_archive(
            ctx, entity.id, document_id, owner_user_id=user.id, owner_company_id=None
        )
        return CommandResult(200, {"vehicle": owner_view.detail(user.id, entity.public_id)})

    payload = {"vehicle": entity.public_id, "document_id": str(document_id)}
    actor = Actor(user_id=user.id, session_id=session.id)
    return run_and_respond(request, "cabinet.vehicle_document_archive", actor, payload, handler)


@require_POST
def pass_documents(request: HttpRequest, vehicle_id: str) -> JsonResponse:
    """The owner passes copies of his documents to a company whose management he confirmed."""
    user, session = require_user(request)
    entity = owner_view.vehicle_of(user.id, vehicle_id.upper())
    data = parse_json(request)
    card_id = _uuid(data.get("card_id"), "card_id")
    documents = document_transfer.parse_ids(data.get("document_ids"))
    if card_id is None or not documents:
        raise errors.validation_error("document_ids", code="FIELD_REQUIRED")

    def handler(ctx: CommandContext) -> CommandResult:
        owner_view.pass_documents(ctx, user.id, entity, card_id, documents)
        return CommandResult(200, {"vehicle": owner_view.detail(user.id, entity.public_id)})

    payload = {
        "vehicle": entity.public_id,
        "card_id": str(card_id),
        "document_ids": sorted(str(d) for d in documents),
    }
    actor = Actor(user_id=user.id, session_id=session.id)
    return run_and_respond(request, "cabinet.vehicle_documents_pass", actor, payload, handler)


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
