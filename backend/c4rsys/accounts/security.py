"""Generation and hashing of secrets. Plain values are never stored (ТЗ § 24.4.3)."""

import hashlib
import hmac
import secrets

from django.conf import settings

from c4rsys.platform.ids import ALPHABET

CODE_DIGITS = 6  # technical choice; the ТЗ fixes validity and attempts, not the length


def new_token() -> tuple[str, str]:
    """Random bearer token for a session or an auth flow and its storage hash."""
    token = secrets.token_urlsafe(32)
    return token, token_hash(token)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def generate_code() -> str:
    return f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"


def code_hash(code: str) -> str:
    """Keyed hash: a leaked table alone does not allow checking the short code space."""
    return hmac.new(settings.SECRET_KEY.encode(), code.encode(), hashlib.sha256).hexdigest()


def code_matches(code: str, stored_hash: str) -> bool:
    return hmac.compare_digest(code_hash(code), stored_hash)


def generate_backup_code() -> str:
    """One-time backup recovery code shown once after the password is set (§ 2.1.3)."""
    raw = "".join(secrets.choice(ALPHABET) for _ in range(16))
    return "-".join(raw[i : i + 4] for i in range(0, 16, 4))
