"""The window of the bot — a Telegram Mini App (customer's decision of 23.11.2026): the pages of
the site opened inside Telegram, signed in by the Telegram account bound to the profile, without
the code and the password.

Telegram gives the page a line of launch data signed with the token of the bot. The line is
checked here as Telegram describes: the secret is HMAC-SHA256 of the token with the key
«WebAppData», the signature is HMAC-SHA256 of the sorted «key=value» lines with that secret. A
line older than `MAX_AGE` is refused, so a line that leaked does not open the profile later."""

import hashlib
import hmac
import json
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qsl

from django.conf import settings
from django.utils import timezone

from c4rsys.platform import errors

# PROPOSAL: how long the launch data of a window opens a session.
MAX_AGE = timedelta(hours=1)


def sign(fields: dict[str, str], token: str) -> str:
    """The signature Telegram puts into the launch data."""
    check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    return hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()


def telegram_user(init_data: str) -> dict[str, Any]:
    """The Telegram user of valid launch data; anything else is refused."""
    refused = errors.ApiError("SESSION_REQUIRED", status=401)
    fields = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=False))
    given = fields.pop("hash", "")
    token = settings.C4R_TELEGRAM_BOT_TOKEN
    if not token or not given or not hmac.compare_digest(given, sign(fields, token)):
        raise refused
    try:
        at = int(fields["auth_date"])
        user = json.loads(fields["user"])
        user_id = int(user["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise refused from exc
    age = timezone.now().timestamp() - at
    if age > MAX_AGE.total_seconds() or age < -300:
        raise refused
    return {**user, "id": user_id}
