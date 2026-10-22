"""HTTP API of the personal account (docs/stage1/STAGE1_COMMANDS_API.md, «Аккаунт»)."""

from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from c4rsys.accounts import services
from c4rsys.accounts.middleware import (
    current_session,
    mark_session_cleared,
    require_user,
    set_session_cookie,
)
from c4rsys.accounts.models import (
    DEFAULT_IDLE_MINUTES,
    DEFAULT_TOTAL_MINUTES,
    AuthFlow,
    UserAccount,
    UserSession,
)
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult, Handler, run_command
from c4rsys.platform.http import (
    client_ip,
    get_int,
    get_str,
    idempotency_key,
    parse_json,
    respond,
    user_agent,
)
from c4rsys.platform.middleware import get_request_id

LANGUAGES = {"", "en", "ru", "th"}


def _client(request: HttpRequest) -> services.Client:
    return services.Client(ip=client_ip(request), user_agent=user_agent(request))


def _user_payload(user: UserAccount) -> dict[str, Any]:
    email = (
        user.contacts.filter(kind="email")
        .order_by("verified_at")
        .values_list("value", flat=True)
        .first()
    )
    return {
        "public_id": user.public_id,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "middle_name": user.middle_name,
        "has_full_name": user.has_full_name,
        "email": email,
        "language": user.language,
        "theme": user.theme,
    }


def _signed_in_response(signed: services.SignedIn, status: int) -> JsonResponse:
    body: dict[str, Any] = {"user": _user_payload(signed.user)}
    if signed.backup_code is not None:
        body["backup_code"] = signed.backup_code  # shown once (§ 2.1.3)
    response = JsonResponse(body, status=status)
    set_session_cookie(response, signed.session_token, signed.user)
    return response


def _actor(user: UserAccount, session: UserSession) -> Actor:
    return Actor(user_id=user.id, session_id=session.id)


def _run(
    request: HttpRequest,
    name: str,
    actor: Actor,
    payload: dict[str, Any],
    handler: Handler,
) -> JsonResponse:
    return respond(
        run_command(
            command=name,
            actor=actor,
            idempotency_key=idempotency_key(request),
            payload=payload,
            handler=handler,
            request_id=get_request_id(request),
        )
    )


# --- Anonymous auth steps -------------------------------------------------------------------


@require_GET
@ensure_csrf_cookie
def csrf(request: HttpRequest) -> JsonResponse:
    """Sets the CSRF cookie; the interface sends it back in X-CSRFToken (A08)."""
    return JsonResponse({"status": "ok"})


@require_POST
def register_start(request: HttpRequest) -> JsonResponse:
    data = parse_json(request)
    started = services.start_registration(get_str(data, "email"), _client(request))
    return JsonResponse(started.as_dict())


@require_POST
def register_verify(request: HttpRequest) -> JsonResponse:
    data = parse_json(request)
    services.verify_registration_code(get_str(data, "flow"), get_str(data, "code", max_length=20))
    return JsonResponse({"status": "code_confirmed"})


@require_POST
def register_complete(request: HttpRequest) -> JsonResponse:
    data = parse_json(request)
    signed = services.complete_registration(
        get_str(data, "flow"), get_str(data, "password", max_length=128), _client(request)
    )
    return _signed_in_response(signed, status=201)


@require_POST
def login_start(request: HttpRequest) -> JsonResponse:
    data = parse_json(request)
    started = services.start_login(get_str(data, "email"), _client(request))
    return JsonResponse(started.as_dict())


@require_POST
def login_verify(request: HttpRequest) -> JsonResponse:
    data = parse_json(request)
    services.verify_login_code(get_str(data, "flow"), get_str(data, "code", max_length=20))
    return JsonResponse({"status": "code_confirmed"})


@require_POST
def login_complete(request: HttpRequest) -> JsonResponse:
    data = parse_json(request)
    signed = services.complete_login(
        get_str(data, "flow"), get_str(data, "password", max_length=128), _client(request)
    )
    return _signed_in_response(signed, status=200)


@require_POST
def code_resend(request: HttpRequest) -> JsonResponse:
    data = parse_json(request)
    purpose = get_str(data, "purpose", max_length=20)
    if purpose not in {AuthFlow.Purpose.REGISTER, AuthFlow.Purpose.LOGIN}:
        raise errors.validation_error("purpose")
    started = services.resend_code(get_str(data, "flow"), purpose, _client(request))
    return JsonResponse(started.as_dict())


@require_POST
def logout(request: HttpRequest) -> JsonResponse:
    session = current_session(request)
    if session is not None:
        services.end_session(session, UserSession.EndReason.LOGOUT, timezone.now())
    mark_session_cleared(request)
    return JsonResponse({"status": "logged_out"})


# --- Own profile ----------------------------------------------------------------------------


@require_http_methods(["GET", "PATCH"])
def me(request: HttpRequest) -> JsonResponse:
    user, session = require_user(request)
    if request.method == "GET":
        return JsonResponse({"user": _user_payload(user)})

    data = parse_json(request)
    changes: dict[str, str] = {}
    if "language" in data:
        language = get_str(data, "language", max_length=10, required=False)
        if language not in LANGUAGES:
            raise errors.validation_error("language")
        changes["language"] = language
    if "theme" in data:
        theme = get_str(data, "theme", max_length=10)
        if theme not in UserAccount.Theme.values:
            raise errors.validation_error("theme")
        changes["theme"] = theme

    def handler(ctx: CommandContext) -> CommandResult:
        locked = UserAccount.objects.select_for_update().get(pk=user.pk)
        before = {key: getattr(locked, key) for key in changes}
        for key, value in changes.items():
            setattr(locked, key, value)
        locked.save(update_fields=[*changes, "updated_at"])
        ctx.audit(
            "account.settings_changed",
            object_type="user_account",
            object_id=locked.public_id,
            before=before,
            after=changes,
        )
        return CommandResult(200, {"user": _user_payload(locked)})

    return _run(request, "account.update_settings", _actor(user, session), changes, handler)


@require_POST
def confirmation_start(request: HttpRequest) -> JsonResponse:
    """Sends a code for a critical action (§ 2.1.1): personal data change, ending all sessions."""
    user, _session = require_user(request)
    data = parse_json(request)
    started = services.start_confirmation(
        user, get_str(data, "action", max_length=60), _client(request)
    )
    return JsonResponse(started.as_dict())


def _name_is_empty(user: UserAccount) -> bool:
    return not (user.first_name or user.last_name or user.middle_name)


@require_POST
def personal_data(request: HttpRequest) -> JsonResponse:
    """First, last and middle name (§ 2.1.5–2.1.6).

    Changing personal data requires a one-time code (§ 2.1.1). The first entry of the name after
    registration, while first and last name are still empty, does not (decision 22.10.2026).
    """
    user, session = require_user(request)
    data = parse_json(request)
    values = {
        "first_name": get_str(data, "first_name", max_length=150),
        "last_name": get_str(data, "last_name", max_length=150),
        "middle_name": get_str(data, "middle_name", max_length=150, required=False),
    }
    first_entry = _name_is_empty(user)
    if not first_entry:
        services.confirm_critical(
            user,
            "change_personal_data",
            get_str(data, "flow"),
            get_str(data, "code", max_length=20),
        )

    def handler(ctx: CommandContext) -> CommandResult:
        locked = UserAccount.objects.select_for_update().get(pk=user.pk)
        if first_entry and not _name_is_empty(locked):
            # Filled in parallel by another request: this is now a change and needs a code.
            raise errors.business_rule("CONFIRMATION_REQUIRED")
        before = {key: getattr(locked, key) for key in values}
        for key, value in values.items():
            setattr(locked, key, value)
        locked.save(update_fields=[*values, "updated_at"])
        ctx.audit(
            "account.personal_data_changed",
            object_type="user_account",
            object_id=locked.public_id,
            before=before,
            after=values,
        )
        return CommandResult(200, {"user": _user_payload(locked)})

    return _run(request, "account.change_personal_data", _actor(user, session), values, handler)


# --- Sessions and their limits (§ 2.3.3, § 24.4.2) ------------------------------------------


def _session_payload(item: UserSession, current: UserSession) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "created_at": item.created_at.isoformat(),
        "last_activity_at": item.last_activity_at.isoformat(),
        "user_agent": item.user_agent,
        "ip": item.ip,
        "current": item.pk == current.pk,
    }


@require_GET
def sessions(request: HttpRequest) -> JsonResponse:
    user, session = require_user(request)
    items = services.active_sessions(user, timezone.now())
    return JsonResponse({"sessions": [_session_payload(s, session) for s in items]})


@require_POST
def session_end(request: HttpRequest, session_id: UUID) -> JsonResponse:
    user, session = require_user(request)
    target = user.sessions.filter(pk=session_id, ended_at__isnull=True).first()
    if target is None:
        raise errors.not_found()

    def handler(ctx: CommandContext) -> CommandResult:
        now = timezone.now()
        UserSession.objects.filter(pk=target.pk, ended_at__isnull=True).update(
            ended_at=now, end_reason=UserSession.EndReason.ENDED_BY_USER
        )
        ctx.audit(
            "auth.session_ended",
            object_type="user_session",
            object_id=target.id,
            after={"reason": UserSession.EndReason.ENDED_BY_USER},
        )
        return CommandResult(200, {"status": "ended"})

    response = _run(
        request,
        "account.end_session",
        _actor(user, session),
        {"session_id": str(session_id)},
        handler,
    )
    if target.pk == session.pk:
        mark_session_cleared(request)
    return response


@require_POST
def sessions_end_all(request: HttpRequest) -> JsonResponse:
    """Ends every session of the account, the current one included. Requires a code (§ 2.1.1)."""
    user, session = require_user(request)
    data = parse_json(request)
    services.confirm_critical(
        user, "end_all_sessions", get_str(data, "flow"), get_str(data, "code", max_length=20)
    )

    def handler(ctx: CommandContext) -> CommandResult:
        now = timezone.now()
        ids = list(user.sessions.filter(ended_at__isnull=True).values_list("id", flat=True))
        UserSession.objects.filter(pk__in=ids).update(
            ended_at=now, end_reason=UserSession.EndReason.ENDED_ALL
        )
        ctx.audit(
            "auth.all_sessions_ended",
            object_type="user_account",
            object_id=user.public_id,
            after={"sessions": [str(i) for i in ids]},
        )
        return CommandResult(200, {"status": "ended", "count": len(ids)})

    response = _run(request, "account.end_all_sessions", _actor(user, session), {}, handler)
    mark_session_cleared(request)
    return response


def _limits_payload(user: UserAccount) -> dict[str, int]:
    return {
        "idle_minutes": user.session_idle_limit_min,
        "total_minutes": user.session_total_limit_min,
        "max_idle_minutes": DEFAULT_IDLE_MINUTES,
        "max_total_minutes": DEFAULT_TOTAL_MINUTES,
    }


def _save_limits(
    request: HttpRequest,
    user: UserAccount,
    session: UserSession,
    idle: int,
    total: int,
    command: str,
) -> JsonResponse:
    if not 1 <= idle <= DEFAULT_IDLE_MINUTES:
        raise errors.validation_error(
            "idle_minutes", code="OUT_OF_RANGE", min=1, max=DEFAULT_IDLE_MINUTES
        )
    if not 1 <= total <= DEFAULT_TOTAL_MINUTES:
        raise errors.validation_error(
            "total_minutes", code="OUT_OF_RANGE", min=1, max=DEFAULT_TOTAL_MINUTES
        )

    def handler(ctx: CommandContext) -> CommandResult:
        locked = UserAccount.objects.select_for_update().get(pk=user.pk)
        before = _limits_payload(locked)
        locked.session_idle_limit_min = idle
        locked.session_total_limit_min = total
        locked.save(
            update_fields=["session_idle_limit_min", "session_total_limit_min", "updated_at"]
        )
        ctx.audit(
            "account.session_limits_changed",
            object_type="user_account",
            object_id=locked.public_id,
            before=before,
            after=_limits_payload(locked),
        )
        return CommandResult(200, _limits_payload(locked))

    return _run(
        request,
        command,
        _actor(user, session),
        {"idle_minutes": idle, "total_minutes": total},
        handler,
    )


@require_http_methods(["GET", "PUT"])
def session_settings(request: HttpRequest) -> JsonResponse:
    """Account-wide limits for all current and future sessions (§ 24.4.2)."""
    user, session = require_user(request)
    if request.method == "GET":
        return JsonResponse(_limits_payload(user))
    data = parse_json(request)
    return _save_limits(
        request,
        user,
        session,
        get_int(data, "idle_minutes"),
        get_int(data, "total_minutes"),
        "account.set_session_limits",
    )


@require_POST
def session_settings_reset(request: HttpRequest) -> JsonResponse:
    """Back to defaults; login and activity times of sessions are not reset (§ 24.4.2)."""
    user, session = require_user(request)
    return _save_limits(
        request,
        user,
        session,
        DEFAULT_IDLE_MINUTES,
        DEFAULT_TOTAL_MINUTES,
        "account.reset_session_limits",
    )
