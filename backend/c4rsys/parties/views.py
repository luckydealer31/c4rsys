"""HTTP API of the client base and the counterparties (ТЗ § 6.1.11, § 6.2.1–6.2.8)."""

from datetime import date
from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods, require_POST

from c4rsys.accounts.middleware import require_user
from c4rsys.companies import access
from c4rsys.companies import services as companies
from c4rsys.companies.views import run_and_respond
from c4rsys.parties import counterparties, services
from c4rsys.parties.models import ClientCard, Counterparty
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import get_int, get_str, parse_json

NAME_MAX = 150
COMPANY_NAME_MAX = 200
LIST_LIMIT = 100


def _actor(request: HttpRequest, cmp: str) -> tuple[companies.CompanyContext, Actor]:
    user, session = require_user(request)
    context = companies.resolve_company(user, cmp, session.id)
    return context, companies.company_actor(user, context, session.id)


def _has(actor: Actor, *permissions: str) -> bool:
    """Clients belong to the whole company (§ 6.1.9): the permission is checked without a
    location."""
    resolver = access.RightsResolver()
    return any(resolver.has_permission(actor, p) for p in permissions)


def _require(actor: Actor, permission: str) -> None:
    if not _has(actor, permission):
        raise errors.permission_denied(permission)


def _payload(card: ClientCard) -> dict[str, Any]:
    return {"id": str(card.id), "version": card.version, **services.snapshot(card)}


def _birth_date(data: dict[str, Any]) -> date | None:
    value = data.get("birth_date")
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise errors.validation_error("birth_date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise errors.validation_error("birth_date", code="DATE_INVALID") from exc


def _name(data: dict[str, Any], key: str, max_length: int = NAME_MAX) -> str:
    return get_str(data, key, max_length=max_length, required=False)


# Optional texts of the cards with their maximum length (§ 22.3.2–22.3.3).
CLIENT_TEXTS = {"address": 500, "comment": 2000, "external_id": 100}
COUNTERPARTY_TEXTS = {"registration": 500, "address": 500, "category": 100, "external_id": 100}


def _texts(data: dict[str, Any], fields: dict[str, int], only_given: bool) -> dict[str, str]:
    return {
        key: get_str(data, key, max_length=length, required=False)
        for key, length in fields.items()
        if key in data or not only_given
    }


@require_http_methods(["GET", "POST"])
def client_list(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        # Viewing lists is separate from finding a client being served (§ 6.2.2).
        _require(actor, "client.list")
        text = request.GET.get("q", "")[:100]
        items = [_payload(c) for c in services.search(context.company.id, text)[:LIST_LIMIT]]
        return JsonResponse({"clients": items})

    _require(actor, "client.create")
    data = parse_json(request)
    client = services.ClientData(
        kind=get_str(data, "kind", max_length=10),
        first_name=_name(data, "first_name"),
        last_name=_name(data, "last_name"),
        middle_name=_name(data, "middle_name"),
        birth_date=_birth_date(data),
        company_name=_name(data, "company_name", COMPANY_NAME_MAX),
        **_texts(data, CLIENT_TEXTS, only_given=False),
    )
    client.check_minimum()
    contacts = services.parse_contacts(data.get("contacts", []), required=True)

    def handler(ctx: CommandContext) -> CommandResult:
        card = services.create_client(ctx, context.company, client, contacts)
        query = services.MatchQuery(
            first_name=client.first_name,
            last_name=client.last_name,
            birth_date=client.birth_date,
            company_name=client.company_name,
            contacts=tuple(contacts),
        )
        others = [str(c.id) for c, _ in services.matches(context.company.id, query) if c != card]
        warnings = [{"code": "POSSIBLE_DUPLICATES", "clients": others}] if others else []
        card = services.get_client(context.company.id, card.id)
        return CommandResult(201, {"client": _payload(card), "warnings": warnings})

    payload = {
        "kind": client.kind,
        "first_name": client.first_name,
        "last_name": client.last_name,
        "middle_name": client.middle_name,
        "birth_date": client.birth_date.isoformat() if client.birth_date else None,
        "company_name": client.company_name,
        **{key: getattr(client, key) for key in CLIENT_TEXTS},
        "contacts": [c.as_dict() for c in contacts],
    }
    return run_and_respond(request, "client.create", actor, payload, handler)


@require_http_methods(["GET", "PATCH"])
def client_detail(request: HttpRequest, cmp: str, client_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        if not _has(actor, "client.find", "client.list"):
            raise errors.permission_denied("client.find")
        return JsonResponse(
            {"client": _payload(services.get_client(context.company.id, client_id))}
        )

    _require(actor, "client.edit")
    services.get_client(context.company.id, client_id)
    data = parse_json(request)
    if "kind" in data:
        raise errors.validation_error("kind", code="CLIENT_KIND_IMMUTABLE")
    expected_version = get_int(data, "expected_version")
    changes: dict[str, Any] = {}
    for key in ("first_name", "last_name", "middle_name"):
        if key in data:
            changes[key] = _name(data, key)
    if "company_name" in data:
        changes["company_name"] = _name(data, "company_name", COMPANY_NAME_MAX)
    if "birth_date" in data:
        changes["birth_date"] = _birth_date(data)
    changes.update(_texts(data, CLIENT_TEXTS, only_given=True))
    contacts = (
        services.parse_contacts(data["contacts"], required=True) if "contacts" in data else None
    )
    if not changes and contacts is None:
        raise errors.validation_error("body", code="NOTHING_TO_CHANGE")

    def handler(ctx: CommandContext) -> CommandResult:
        services.update_client(
            ctx, context.company.id, client_id, changes, contacts, expected_version
        )
        return CommandResult(
            200, {"client": _payload(services.get_client(context.company.id, client_id))}
        )

    payload: dict[str, Any] = {
        "client_id": str(client_id),
        **{k: (v.isoformat() if isinstance(v, date) else v) for k, v in changes.items()},
        "expected_version": expected_version,
    }
    if contacts is not None:
        payload["contacts"] = [c.as_dict() for c in contacts]
    return run_and_respond(request, "client.update", actor, payload, handler)


@require_POST
def client_matches(request: HttpRequest, cmp: str) -> JsonResponse:
    """Search for an existing card while entering a client (§ 6.1.11). A read: no business
    operation, no Idempotency-Key. POST keeps personal data out of the URL."""
    context, actor = _actor(request, cmp)
    _require(actor, "client.find")
    data = parse_json(request)
    contacts: list[services.Contact] = []
    for kind in ("phone", "email"):
        value = data.get(kind)
        if isinstance(value, str) and value.strip():
            contacts.append(services.normalize_contact(kind, value, kind))
    query = services.MatchQuery(
        first_name=_name(data, "first_name"),
        last_name=_name(data, "last_name"),
        birth_date=_birth_date(data),
        company_name=_name(data, "company_name", COMPANY_NAME_MAX),
        contacts=tuple(contacts),
    )
    found = services.matches(context.company.id, query)
    return JsonResponse(
        {"matches": [{"client": _payload(c), "matched": reasons} for c, reasons in found]}
    )


# --- Counterparties (§ 6.1.4–6.1.9, § 23.8.2; customer's decision of 25.10.2026) ----------------

COMMENT_MAX = 2000


def _counterparty_payload(card: Counterparty) -> dict[str, Any]:
    return {
        "id": str(card.id),
        "version": card.version,
        "name": counterparties.display_name(card),
        **counterparties.snapshot(card),
    }


@require_http_methods(["GET", "POST"])
def counterparty_list(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        _require(actor, "counterparty.view")
        state = request.GET.get("state", Counterparty.State.ACTIVE)
        if state not in ("", *Counterparty.State.values):
            raise errors.validation_error("state")
        text = request.GET.get("q", "")[:100]
        found = counterparties.search(context.company.id, text, state)[:LIST_LIMIT]
        return JsonResponse({"counterparties": [_counterparty_payload(c) for c in found]})

    _require(actor, "counterparty.create")
    data = parse_json(request)
    card_data = counterparties.CounterpartyData(
        kind=get_str(data, "kind", max_length=10),
        first_name=_name(data, "first_name"),
        last_name=_name(data, "last_name"),
        middle_name=_name(data, "middle_name"),
        company_name=_name(data, "company_name", COMPANY_NAME_MAX),
        comment=get_str(data, "comment", max_length=COMMENT_MAX, required=False),
        **_texts(data, COUNTERPARTY_TEXTS, only_given=False),
    )
    card_data.check_minimum()
    contacts = services.parse_contacts(data.get("contacts", []), required=True)

    def handler(ctx: CommandContext) -> CommandResult:
        card = counterparties.create(ctx, context.company, card_data, contacts)
        card = counterparties.get(context.company.id, card.id)
        return CommandResult(201, {"counterparty": _counterparty_payload(card)})

    payload = {
        "kind": card_data.kind,
        "first_name": card_data.first_name,
        "last_name": card_data.last_name,
        "middle_name": card_data.middle_name,
        "company_name": card_data.company_name,
        "comment": card_data.comment,
        **{key: getattr(card_data, key) for key in COUNTERPARTY_TEXTS},
        "contacts": [c.as_dict() for c in contacts],
    }
    return run_and_respond(request, "counterparty.create", actor, payload, handler)


@require_http_methods(["GET", "PATCH"])
def counterparty_detail(request: HttpRequest, cmp: str, counterparty_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        _require(actor, "counterparty.view")
        card = counterparties.get(context.company.id, counterparty_id)
        return JsonResponse({"counterparty": _counterparty_payload(card)})

    _require(actor, "counterparty.edit")
    counterparties.get(context.company.id, counterparty_id)
    data = parse_json(request)
    if "kind" in data:
        raise errors.validation_error("kind", code="COUNTERPARTY_KIND_IMMUTABLE")
    expected_version = get_int(data, "expected_version")
    changes: dict[str, Any] = {}
    for key in ("first_name", "last_name", "middle_name"):
        if key in data:
            changes[key] = _name(data, key)
    if "company_name" in data:
        changes["company_name"] = _name(data, "company_name", COMPANY_NAME_MAX)
    if "comment" in data:
        changes["comment"] = get_str(data, "comment", max_length=COMMENT_MAX, required=False)
    changes.update(_texts(data, COUNTERPARTY_TEXTS, only_given=True))
    contacts = (
        services.parse_contacts(data["contacts"], required=True) if "contacts" in data else None
    )
    if not changes and contacts is None:
        raise errors.validation_error("body", code="NOTHING_TO_CHANGE")

    def handler(ctx: CommandContext) -> CommandResult:
        counterparties.update(
            ctx, context.company.id, counterparty_id, changes, contacts, expected_version
        )
        card = counterparties.get(context.company.id, counterparty_id)
        return CommandResult(200, {"counterparty": _counterparty_payload(card)})

    payload: dict[str, Any] = {
        "counterparty_id": str(counterparty_id),
        **changes,
        "expected_version": expected_version,
    }
    if contacts is not None:
        payload["contacts"] = [c.as_dict() for c in contacts]
    return run_and_respond(request, "counterparty.update", actor, payload, handler)


def _counterparty_state(
    request: HttpRequest, cmp: str, counterparty_id: UUID, archived: bool
) -> JsonResponse:
    """Archiving and restoring are separate rights (§ 6.6.4, § 23.8.2)."""
    context, actor = _actor(request, cmp)
    _require(actor, "counterparty.archive" if archived else "counterparty.restore")
    expected_version = get_int(parse_json(request), "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        counterparties.set_archived(
            ctx, context.company.id, counterparty_id, archived, expected_version
        )
        card = counterparties.get(context.company.id, counterparty_id)
        return CommandResult(200, {"counterparty": _counterparty_payload(card)})

    payload = {"counterparty_id": str(counterparty_id), "expected_version": expected_version}
    name = "counterparty.archive" if archived else "counterparty.restore"
    return run_and_respond(request, name, actor, payload, handler)


@require_POST
def counterparty_archive(request: HttpRequest, cmp: str, counterparty_id: UUID) -> JsonResponse:
    return _counterparty_state(request, cmp, counterparty_id, archived=True)


@require_POST
def counterparty_restore(request: HttpRequest, cmp: str, counterparty_id: UUID) -> JsonResponse:
    return _counterparty_state(request, cmp, counterparty_id, archived=False)
