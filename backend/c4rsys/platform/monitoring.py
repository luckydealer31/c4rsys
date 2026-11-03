"""Server errors to Sentry (audit item 21, customer decision 03.11.2026). Off without SENTRY_DSN.

Only errors go out, without personal data: no cookies, headers of the visitor, request bodies or
local variables of the code (they may hold an email, a password or a document), and the secret
parts of links (signing, password setting) are cut out of addresses. Unhandled errors of requests
and of the background jobs (rates, deposits, schedule) and every log record of level ERROR become
events."""

import re
from typing import Any

SECRET_PATH = re.compile(r"(/api/(?:sign|recovery/password)/)[^/?#]+")


def _cut(value: Any) -> Any:
    return SECRET_PATH.sub(r"\1[cut]", value) if isinstance(value, str) else value


def scrub(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    request = event.get("request")
    if isinstance(request, dict):
        if "url" in request:
            request["url"] = _cut(request["url"])
        request.pop("query_string", None)
        request.pop("cookies", None)
        request.pop("data", None)
        request.pop("headers", None)
        request.pop("env", None)
    if "transaction" in event:
        event["transaction"] = _cut(event["transaction"])
    for crumb in (event.get("breadcrumbs") or {}).get("values", []):
        crumb["message"] = _cut(crumb.get("message"))
        if isinstance(crumb.get("data"), dict):
            crumb["data"] = {k: _cut(v) for k, v in crumb["data"].items()}
    return event


def init(dsn: str, environment: str, release: str) -> None:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release or None,
        integrations=[DjangoIntegration()],
        data_collection={
            "user_info": False,
            "cookies": {"mode": "off"},
            "http_headers": {"request": {"mode": "off"}},
            "http_bodies": [],
            "url_query_params": {"mode": "off"},
            "database_query_data": False,
            "stack_frame_variables": False,
        },
        max_request_body_size="never",
        # Errors only; tracing of speed is not turned on (PROPOSAL, агент, не подтверждено).
        traces_sample_rate=0,
        before_send=scrub,  # type: ignore[arg-type]
    )
