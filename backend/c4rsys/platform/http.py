"""Small JSON helpers for API views (A08). Reads never perform business operations."""

import json
from typing import Any

from django.http import HttpRequest, JsonResponse

from c4rsys.platform import errors
from c4rsys.platform.commands import CommandResult

IDEMPOTENCY_HEADER = "HTTP_IDEMPOTENCY_KEY"


def parse_json(request: HttpRequest) -> dict[str, Any]:
    if not request.body:
        return {}
    try:
        data = json.loads(request.body)
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
    value = request.META.get("REMOTE_ADDR")
    return str(value) if value else None


def user_agent(request: HttpRequest) -> str:
    return str(request.META.get("HTTP_USER_AGENT", ""))[:300]


def respond(result: CommandResult) -> JsonResponse:
    return JsonResponse(result.body, status=result.status)
