"""Cloudflare Turnstile: a check that a person, not a bot, asks to send a code to an address
typed on an open form (customer decision 02.11.2026). Off while the secret key is not set."""

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from django.conf import settings

from c4rsys.platform import errors

logger = logging.getLogger(__name__)

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TIMEOUT_SECONDS = 10
TOKEN_MAX_LENGTH = 2048


def enabled() -> bool:
    return bool(settings.C4R_TURNSTILE_SECRET_KEY and settings.C4R_TURNSTILE_SITE_KEY)


def site_key() -> str | None:
    """The public key for the form widget; none while the check is off."""
    return str(settings.C4R_TURNSTILE_SITE_KEY) if enabled() else None


def _verify(token: str) -> dict[str, Any]:
    body = urllib.parse.urlencode(
        {"secret": settings.C4R_TURNSTILE_SECRET_KEY, "response": token}
    ).encode()
    # The address is the constant https URL of Cloudflare.
    request = urllib.request.Request(VERIFY_URL, data=body, method="POST")  # noqa: S310
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
        data: dict[str, Any] = json.load(response)
    return data


def require_human(data: dict[str, Any]) -> None:
    """Refuses the request unless the form carries a token Cloudflare accepts. When Cloudflare
    cannot be reached the request is refused too: the widget would not load either
    (PROPOSAL, агент, не подтверждено)."""
    if not enabled():
        return
    token = data.get("captcha")
    if not isinstance(token, str) or not token or len(token) > TOKEN_MAX_LENGTH:
        raise errors.validation_error("captcha", code="CAPTCHA_REQUIRED")
    try:
        answer = _verify(token)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        logger.warning("Turnstile check failed: %s", type(exc).__name__)
        raise errors.ApiError("CAPTCHA_UNAVAILABLE", status=503, field="captcha") from exc
    if answer.get("success") is not True:
        raise errors.validation_error("captcha", code="CAPTCHA_FAILED")
