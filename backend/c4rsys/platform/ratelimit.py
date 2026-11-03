"""Limits of requests from one address to the open forms (audit item 11): sending a code, checking a
code or a password, the requests of access recovery and the external signing links. They add to
the limits per contact (§ 2.4.4) and per flow (5 attempts), which a bot gets round by changing the
address it types. The numbers are PROPOSAL (агент, не подтверждено).

Counts live in fixed hourly windows in the database, so all gunicorn workers share them. A count
is written outside the transaction of the request: a refused or failed request still counts."""

import logging
import re
from collections.abc import Callable
from datetime import datetime, timedelta

from django.db import connection
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone

from c4rsys.platform.errors import ApiError
from c4rsys.platform.http import client_ip
from c4rsys.platform.middleware import get_request_id

logger = logging.getLogger(__name__)

WINDOW = timedelta(hours=1)
KEEP = timedelta(days=1)

# scope → requests from one address in an hour.
LIMITS = {
    "code_send": 30,
    "code_check": 120,
}

RULES = [
    (
        "code_send",
        re.compile(
            r"^/api/(auth/(register|login)/start|auth/code/resend|recovery/email/start"
            r"|sign/[^/]+/code)$"
        ),
    ),
    (
        "code_check",
        re.compile(
            r"^/api/(auth/(register|login)/(verify|complete)|recovery/(email/verify|requests"
            r"|password(/[^/]+)?)|sign/[^/]+/decide)$"
        ),
    ),
]


def scope_of(path: str) -> str | None:
    for scope, pattern in RULES:
        if pattern.match(path):
            return scope
    return None


def count(ip: str, scope: str) -> tuple[int, datetime]:
    """Adds the request to its window; returns the count and the end of the window."""
    now = timezone.now()
    start = now.replace(minute=0, second=0, microsecond=0)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO ip_request_count (ip, scope, window_start, count) VALUES (%s, %s, %s, 1)
            ON CONFLICT (ip, scope, window_start) DO UPDATE SET count = ip_request_count.count + 1
            RETURNING count
            """,
            [ip, scope, start],
        )
        row = cursor.fetchone()
        value = int(row[0]) if row else 1
        if value == 1:
            # The first request of a new window clears the windows nobody needs any more.
            cursor.execute("DELETE FROM ip_request_count WHERE window_start < %s", [now - KEEP])
    return value, start + WINDOW


class RateLimitMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        scope = scope_of(request.path) if request.method == "POST" else None
        ip = client_ip(request) if scope else None
        if scope and ip:
            value, retry_at = count(ip, scope)
            if value > LIMITS[scope]:
                if value == LIMITS[scope] + 1:
                    logger.warning("Request limit %s reached from %s", scope, ip)
                error = ApiError(
                    "IP_RATE_LIMITED", status=429, params={"retry_at": retry_at.isoformat()}
                )
                response = JsonResponse(error.payload(get_request_id(request)), status=429)
                response["Retry-After"] = str(
                    max(int((retry_at - timezone.now()).total_seconds()), 1)
                )
                return response
        return self.get_response(request)
