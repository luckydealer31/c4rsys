"""Outgoing messages with one-time codes.

The provider for the test server is OPEN (docs/stage1/EMAIL_PROVIDER.md). In development codes are
written to the backend log. Messages are sent only after the transaction commits.
"""

import logging

from django.conf import settings

logger = logging.getLogger("c4rsys.mail")


def send_code(email: str, code: str, purpose: str) -> None:
    mode = getattr(settings, "C4R_MAIL_MODE", "console")
    if mode == "console":
        logger.warning("DEV MAIL to %s: %s code %s", email, purpose, code)
        return
    raise NotImplementedError(f"Mail mode {mode!r} is not configured yet")
