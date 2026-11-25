"""HTTP API of the client base and the counterparties (ТЗ § 6.1.11, § 6.2.1–6.2.8)."""

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
from c4rsys.parties import counterparties, extended, services
from c4rsys.parties.models import ClientCard, ClientRestriction, Counterparty
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import get_int, get_str, parse_json
from c4rsys.tags import services as tags

NAME_MAX = 150
COMPANY_NAME_MAX = 200
LIST_LIMIT = 100
COUNTERPARTY = counterparties.COUNTERPARTY


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


def _payload(card: ClientCard, blacklisted: bool | None = None) -> dict[str, Any]:
    if blacklisted is None:
        blacklisted = services.active_restrictions(
            card.company_id, services.family_ids(card.company_id, card.id)
        ).exists()
    return {
        "id": str(card.id),
        "version": card.version,
        "name": services.display_name(card),
        **services.snapshot(card),
        # § 6.7.1: on the blacklist of the company (a mark of a card merged into it counts).
        "blacklisted": blacklisted,
    }


def _payloads(company_id: UUID, cards: list[ClientCard]) -> list[dict[str, Any]]:
    marked = set(
        services.active_restrictions(company_id, [c.id for c in cards]).values_list(
            "client_id", flat=True
        )
    )
    return [_payload(c, c.id in marked) for c in cards]


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
        # § 6.6.4: the usual list or the «Archive» filter.
        state = request.GET.get("state", ClientCard.State.ACTIVE)
        if state not in (ClientCard.State.ACTIVE, ClientCard.State.ARCHIVED):
            raise errors.validation_error("state")
        also = tags.matching_ids(context.company.id, actor.user_id, "client", text)
        found = list(services.search(context.company.id, text, state, also)[:LIST_LIMIT])
        return JsonResponse({"clients": _payloads(context.company.id, found)})

    _require(actor, "client.create")
    data = parse_json(request)
    kind = get_str(data, "kind", max_length=10)
    client = services.ClientData(
        kind=kind,
        first_name=_name(data, "first_name"),
        last_name=_name(data, "last_name"),
        middle_name=_name(data, "middle_name"),
        birth_date=_birth_date(data),
        company_name=_name(data, "company_name", COMPANY_NAME_MAX),
        **_texts(data, CLIENT_TEXTS, only_given=False),
        extended=extended.parse(data, kind, only_given=False),
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
        **extended.as_payload(client.extended),
        "contacts": [c.as_dict() for c in contacts],
    }
    return run_and_respond(request, "client.create", actor, payload, handler)


@require_http_methods(["GET", "PATCH"])
def client_detail(request: HttpRequest, cmp: str, client_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        if not _has(actor, "client.find", "client.list"):
            raise errors.permission_denied("client.find")
        card = services.get_client(context.company.id, client_id)
        body = _payload(card)
        # With whom and by what the card matches (customer's remark of 25.11.2026).
        body["duplicates"] = services.duplicates_of(card) if card.possible_duplicate else []
        return JsonResponse({"client": body})

    _require(actor, "client.edit")
    current = services.get_client(context.company.id, client_id)
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
    changes.update(extended.parse(data, current.kind, only_given=True))
    contacts = (
        services.parse_contacts(data["contacts"], required=not current.provisional)
        if "contacts" in data
        else None
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
        **extended.as_payload(changes),
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
        loose=data.get("loose") is True,
    )
    found = services.matches(context.company.id, query)
    payloads = _payloads(context.company.id, [c for c, _ in found])
    return JsonResponse(
        {
            "matches": [
                {"client": item, "matched": reasons}
                for item, (_c, reasons) in zip(payloads, found, strict=True)
            ]
        }
    )


@require_POST
def counterparty_matches(request: HttpRequest, cmp: str, role: str = COUNTERPARTY) -> JsonResponse:
    """Existing counterparties for the entered data, as for the clients (§ 6.1.11): the owner of
    a vehicle entered in its form is checked so (customer's remark of 25.11.2026). A read."""
    context, actor = _actor(request, cmp)
    _require(actor, f"{role}.view")
    data = parse_json(request)
    contacts: list[services.Contact] = []
    for kind in ("phone", "email"):
        value = data.get(kind)
        if isinstance(value, str) and value.strip():
            contacts.append(services.normalize_contact(kind, value, kind))
    query = services.MatchQuery(
        first_name=_name(data, "first_name"),
        last_name=_name(data, "last_name"),
        company_name=_name(data, "company_name", COMPANY_NAME_MAX),
        contacts=tuple(contacts),
        loose=data.get("loose") is True,
    )
    found = counterparties.matches(context.company.id, query, role)
    return JsonResponse(
        {
            "matches": [
                {"counterparty": _counterparty_payload(card), "matched": reasons}
                for card, reasons in found
            ]
        }
    )


# --- Archive (§ 6.6.4–6.6.5, § 23.8.2) -----------------------------------------------------------


def _client_state(request: HttpRequest, cmp: str, client_id: UUID, archived: bool) -> JsonResponse:
    """Archiving and restoring are separate rights (§ 23.8.2)."""
    context, actor = _actor(request, cmp)
    permission = "client.archive" if archived else "client.restore"
    _require(actor, permission)
    expected_version = get_int(parse_json(request), "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        services.set_archived(ctx, context.company.id, client_id, archived, expected_version)
        return CommandResult(
            200, {"client": _payload(services.get_client(context.company.id, client_id))}
        )

    payload = {"client_id": str(client_id), "expected_version": expected_version}
    return run_and_respond(request, permission, actor, payload, handler)


@require_POST
def client_archive(request: HttpRequest, cmp: str, client_id: UUID) -> JsonResponse:
    return _client_state(request, cmp, client_id, archived=True)


@require_POST
def client_restore(request: HttpRequest, cmp: str, client_id: UUID) -> JsonResponse:
    return _client_state(request, cmp, client_id, archived=False)


# --- Blacklist (§ 6.7.1, § 23.8.7) ---------------------------------------------------------------

REASON_MAX = 1000


@require_http_methods(["GET", "POST"])
def client_restrictions(request: HttpRequest, cmp: str, client_id: UUID) -> JsonResponse:
    """The marks of the card and of the cards merged into it, with their history."""
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        if not _has(actor, "client.find", "client.list"):
            raise errors.permission_denied("client.find")
        services.get_client(context.company.id, client_id)
        family = services.family_ids(context.company.id, client_id)
        marks = list(
            ClientRestriction.objects.filter(
                company_id=context.company.id, client_id__in=family
            ).order_by("-added_at")
        )
        from c4rsys.accounts import services as accounts

        names = accounts.names(
            {m.added_by_user_id for m in marks}
            | {m.lifted_by_user_id for m in marks if m.lifted_by_user_id}
        )
        return JsonResponse(
            {"restrictions": [services.restriction_payload(m, names) for m in marks]}
        )

    _require(actor, "client.blacklist.add")
    reason = get_str(parse_json(request), "reason", max_length=REASON_MAX)

    def handler(ctx: CommandContext) -> CommandResult:
        mark = services.add_restriction(ctx, context.company.id, client_id, reason)
        return CommandResult(201, {"restriction": services.restriction_payload(mark, {})})

    payload = {"client_id": str(client_id), "reason": reason}
    return run_and_respond(request, "client.blacklist.add", actor, payload, handler)


@require_POST
def restriction_lift(request: HttpRequest, cmp: str, restriction_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _require(actor, "client.blacklist.remove")
    reason = get_str(parse_json(request), "reason", max_length=REASON_MAX)

    def handler(ctx: CommandContext) -> CommandResult:
        mark = services.lift_restriction(ctx, context.company.id, restriction_id, reason)
        return CommandResult(200, {"restriction": services.restriction_payload(mark, {})})

    payload = {"restriction_id": str(restriction_id), "reason": reason}
    return run_and_respond(request, "client.blacklist.remove", actor, payload, handler)


# --- Provisional card with a pending link (§ 6.2.9–6.2.11, § 6.1.13) -----------------------------


def _provisional(
    request: HttpRequest, cmp: str, card_type: str, role: str = COUNTERPARTY
) -> JsonResponse:
    """Instead of filling the card by hand: a card with only the ID, email or phone of the
    profile and the request sent to it at once. Creating the card and sending the request are
    both needed (§ 23.8.2)."""
    from c4rsys.parties import links

    context, actor = _actor(request, cmp)
    create, link = (
        ("client.create", "client.link.request")
        if card_type == "client"
        else (f"{role}.create", f"{role}.link.request")
    )
    _require(actor, create)
    _require(actor, link)
    data = parse_json(request)
    kind = get_str(data, "kind", max_length=10)
    if kind not in ("person", "company"):
        raise errors.validation_error("kind")
    target_text = get_str(data, "target", max_length=254)

    def handler(ctx: CommandContext) -> CommandResult:
        target = links.resolve_target(kind, target_text)
        contacts = []
        if target.email:
            contacts.append(services.normalize_contact("email", target.email, "target"))
        if target.phone:
            contacts.append(services.normalize_contact("phone", target.phone, "target"))
        if card_type == "client":
            card: Any = services.create_client(
                ctx, context.company, services.ClientData(kind=kind), contacts, provisional=True
            )
        else:
            card = counterparties.create(
                ctx,
                context.company,
                counterparties.CounterpartyData(kind=kind, role=role),
                contacts,
                provisional=True,
            )
        found = links.request_link(ctx, context.company, card_type, card.id, target)
        if card_type == "client":
            body = {"client": _payload(services.get_client(context.company.id, card.id))}
        else:
            body = {
                "counterparty": _counterparty_payload(
                    counterparties.get(context.company.id, card.id)
                )
            }
        return CommandResult(201, {**body, "link": links.payload(found, timezone.now())})

    name = card_type if card_type == "client" else role
    payload = {"card_type": name, "kind": kind, "target": target_text}
    return run_and_respond(request, f"{name}.create_provisional", actor, payload, handler)


@require_POST
def client_provisional(request: HttpRequest, cmp: str) -> JsonResponse:
    return _provisional(request, cmp, "client")


@require_POST
def counterparty_provisional(
    request: HttpRequest, cmp: str, role: str = COUNTERPARTY
) -> JsonResponse:
    return _provisional(request, cmp, "counterparty", role)


# --- Counterparties (§ 6.1.4–6.1.9, § 23.8.2; customer's decision of 25.10.2026) ----------------

COMMENT_MAX = 2000


def _counterparty_payload(card: Counterparty) -> dict[str, Any]:
    return {
        "id": str(card.id),
        "version": card.version,
        "name": counterparties.display_name(card),
        **counterparties.snapshot(card),
    }


@require_http_methods(["GET"])
def counterparty_categories(
    request: HttpRequest, cmp: str, role: str = COUNTERPARTY
) -> JsonResponse:
    """The categories of counterparties of the company, to choose from and to filter by."""
    context, actor = _actor(request, cmp)
    resolver_rights = (f"{role}.view", f"{role}.create", f"{role}.edit")
    if not any(access.RightsResolver().has_permission(actor, p) for p in resolver_rights):
        raise errors.permission_denied(f"{role}.view")
    return JsonResponse({"categories": counterparties.categories(context.company.id, role)})


@require_http_methods(["GET", "POST"])
def counterparty_list(request: HttpRequest, cmp: str, role: str = COUNTERPARTY) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        _require(actor, f"{role}.view")
        state = request.GET.get("state", Counterparty.State.ACTIVE)
        if state not in ("", *Counterparty.State.values):
            raise errors.validation_error("state")
        text = request.GET.get("q", "")[:100]
        category = request.GET.get("category", "")[:100]
        also = tags.matching_ids(context.company.id, actor.user_id, "counterparty", text)
        found = counterparties.search(context.company.id, text, state, category, also, role)[
            :LIST_LIMIT
        ]
        return JsonResponse({"counterparties": [_counterparty_payload(c) for c in found]})

    _require(actor, f"{role}.create")
    data = parse_json(request)
    kind = get_str(data, "kind", max_length=10)
    card_data = counterparties.CounterpartyData(
        kind=kind,
        role=role,
        first_name=_name(data, "first_name"),
        last_name=_name(data, "last_name"),
        middle_name=_name(data, "middle_name"),
        company_name=_name(data, "company_name", COMPANY_NAME_MAX),
        birth_date=_birth_date(data),
        comment=get_str(data, "comment", max_length=COMMENT_MAX, required=False),
        **_texts(data, COUNTERPARTY_TEXTS, only_given=False),
        extended=extended.parse(data, kind, only_given=False),
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
        "birth_date": card_data.birth_date.isoformat() if card_data.birth_date else None,
        "comment": card_data.comment,
        **{key: getattr(card_data, key) for key in COUNTERPARTY_TEXTS},
        **extended.as_payload(card_data.extended),
        "contacts": [c.as_dict() for c in contacts],
    }
    return run_and_respond(request, f"{role}.create", actor, payload, handler)


@require_http_methods(["GET", "PATCH"])
def counterparty_detail(
    request: HttpRequest, cmp: str, counterparty_id: UUID, role: str = COUNTERPARTY
) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        _require(actor, f"{role}.view")
        card = counterparties.get(context.company.id, counterparty_id, role)
        body = _counterparty_payload(card)
        # With whom and by what the card matches (customer's remark of 25.11.2026).
        body["duplicates"] = counterparties.duplicates_of(card) if card.possible_duplicate else []
        return JsonResponse({"counterparty": body})

    _require(actor, f"{role}.edit")
    current = counterparties.get(context.company.id, counterparty_id, role)
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
    if "birth_date" in data:
        changes["birth_date"] = _birth_date(data)
    changes.update(_texts(data, COUNTERPARTY_TEXTS, only_given=True))
    changes.update(extended.parse(data, current.kind, only_given=True))
    contacts = (
        services.parse_contacts(data["contacts"], required=not current.provisional)
        if "contacts" in data
        else None
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
        **extended.as_payload(changes),
        "expected_version": expected_version,
    }
    if contacts is not None:
        payload["contacts"] = [c.as_dict() for c in contacts]
    return run_and_respond(request, f"{role}.update", actor, payload, handler)


def _counterparty_state(
    request: HttpRequest, cmp: str, counterparty_id: UUID, archived: bool, role: str
) -> JsonResponse:
    """Archiving and restoring are separate rights (§ 6.6.4, § 23.8.2)."""
    context, actor = _actor(request, cmp)
    name = f"{role}.archive" if archived else f"{role}.restore"
    _require(actor, name)
    expected_version = get_int(parse_json(request), "expected_version")
    counterparties.get(context.company.id, counterparty_id, role)

    def handler(ctx: CommandContext) -> CommandResult:
        counterparties.set_archived(
            ctx, context.company.id, counterparty_id, archived, expected_version
        )
        card = counterparties.get(context.company.id, counterparty_id)
        return CommandResult(200, {"counterparty": _counterparty_payload(card)})

    payload = {"counterparty_id": str(counterparty_id), "expected_version": expected_version}
    return run_and_respond(request, name, actor, payload, handler)


@require_POST
def counterparty_archive(
    request: HttpRequest, cmp: str, counterparty_id: UUID, role: str = COUNTERPARTY
) -> JsonResponse:
    return _counterparty_state(request, cmp, counterparty_id, True, role)


@require_POST
def counterparty_restore(
    request: HttpRequest, cmp: str, counterparty_id: UUID, role: str = COUNTERPARTY
) -> JsonResponse:
    return _counterparty_state(request, cmp, counterparty_id, False, role)
