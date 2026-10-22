"""Identifiers.

* Internal primary keys: UUIDv7 (time-ordered), generated in the application.
* Readable c4rsys IDs (ТЗ § 2.1.4, § 2.6.1): prefix, hyphen and a random part from a 32-symbol
  alphabet without I, L, O, U. Initial length: 8 for USR and CMP, 12 for VHL, AST and LOT.
  An issued ID is never reused (registry table, see models.PublicIdRegistry).
"""

import re
import secrets
import time
import uuid

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

INITIAL_LENGTH: dict[str, int] = {
    "USR": 8,
    "CMP": 8,
    "VHL": 12,
    "AST": 12,
    "LOT": 12,
}

_PUBLIC_ID_RE = re.compile(r"^(?P<prefix>[A-Z]{3})-(?P<body>[" + ALPHABET + r"]+)$")


def uuid7() -> uuid.UUID:
    """UUID version 7 (RFC 9562): 48-bit Unix time in ms, then random bits."""
    unix_ms = time.time_ns() // 1_000_000
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    value = (
        ((unix_ms & ((1 << 48) - 1)) << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    )
    return uuid.UUID(int=value)


def generate_public_id(prefix: str, length: int | None = None) -> str:
    """Random readable ID. Length may later grow without changing the prefix (§ 2.6.1)."""
    if prefix not in INITIAL_LENGTH:
        raise ValueError(f"Unknown public ID prefix: {prefix}")
    size = INITIAL_LENGTH[prefix] if length is None else length
    if size < INITIAL_LENGTH[prefix]:
        raise ValueError("Public ID length cannot be shorter than the initial length")
    body = "".join(secrets.choice(ALPHABET) for _ in range(size))
    return f"{prefix}-{body}"


def normalize_public_id(value: str) -> str | None:
    """Uppercase and validate user input. Returns None for anything that is not a public ID.

    Both the initial and any increased length are accepted (§ 2.6.1).
    """
    candidate = value.strip().upper()
    match = _PUBLIC_ID_RE.match(candidate)
    if match is None:
        return None
    prefix = match.group("prefix")
    if prefix not in INITIAL_LENGTH:
        return None
    if len(match.group("body")) < INITIAL_LENGTH[prefix]:
        return None
    return candidate
