"""Small JSON helpers for API views (A08). Reads never perform business operations."""

import ipaddress
import json
from typing import Any

from django.conf import settings
from django.http import HttpRequest, JsonResponse, RawPostDataException

from c4rsys.platform import errors
from c4rsys.platform.commands import CommandResult

IDEMPOTENCY_HEADER = "HTTP_IDEMPOTENCY_KEY"


def parse_json(request: HttpRequest) -> dict[str, Any]:
    try:
        body = request.body
    except RawPostDataException as exc:
        # A form or a file sent where JSON is expected: the CSRF check has already read it as a
        # form. A wrong request, not a failure of the server (found by Sentry).
        raise errors.validation_error("body", code="INVALID_JSON") from exc
    if not body:
        return {}
    try:
        data = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise errors.validation_error("body", code="INVALID_JSON") from exc
    if not isinstance(data, dict):
        raise errors.validation_error("body", code="INVALID_JSON")
    return data


def get_str(data: dict[str, Any], key: str, *, max_length: int = 254, required: bool = True) -> str:
    value = data.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise errors.validation_error(key, code="FIELD_REQUIRED")
        return ""
    if not isinstance(value, str):
        raise errors.validation_error(key)
    value = value.strip()
    if len(value) > max_length:
        raise errors.validation_error(key, code="FIELD_TOO_LONG", max_length=max_length)
    return value


def get_int(data: dict[str, Any], key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise errors.validation_error(key, code="INTEGER_REQUIRED")
    return value


def idempotency_key(request: HttpRequest) -> str:
    return str(request.META.get(IDEMPOTENCY_HEADER, ""))


def client_ip(request: HttpRequest) -> str | None:
    """The visitor's address (audit item 37). Behind Caddy (C4R_TRUST_PROXY) REMOTE_ADDR is Caddy
    itself; Caddy puts the address it was reached from last in X-Forwarded-For and drops whatever a
    visitor wrote there, so only that last entry is taken. Without the proxy the header is ignored:
    a visitor could write anything into it."""
    if settings.C4R_TRUST_PROXY:
        forwarded = str(request.META.get("HTTP_X_FORWARDED_FOR", "")).split(",")[-1].strip()
        try:
            return str(ipaddress.ip_address(forwarded))
        except ValueError:
            pass
    value = request.META.get("REMOTE_ADDR")
    return str(value) if value else None


def user_agent(request: HttpRequest) -> str:
    return str(request.META.get("HTTP_USER_AGENT", ""))[:300]


def respond(result: CommandResult) -> JsonResponse:
    return JsonResponse(result.body, status=result.status)
