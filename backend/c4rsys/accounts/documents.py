"""The documents of a profile (customer's remarks of 25–26.11.2026).

A person adds the documents they wish, choosing the kind of each: a passport, an ID card, a
driving licence or another one (named). None is required in the profile. Each has a number, the
dates of issue and of expiry and who issued it; a driving licence also has its categories.

PROPOSAL: up to ten documents; the number is needed for a document to be kept; «other» needs a
name; the categories are short codes, kept in capitals, each once.
"""

from datetime import date
from typing import Any

from c4rsys.platform import errors

TYPES = ("passport", "national_id", "driver_license", "other")
IDENTITY = ("passport", "national_id")
LICENCE = "driver_license"
_MAX = 10
_TEXT = 100
_CATEGORY = 10
_CATEGORIES = 20


def _text(value: Any, field: str, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str) or len(value.strip()) > _TEXT:
        raise errors.validation_error(field)
    text = value.strip()
    if required and not text:
        raise errors.validation_error(field, code="FIELD_REQUIRED")
    return text


def _date(value: Any, field: str) -> str | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
        raise errors.validation_error(field, code="DATE_INVALID") from exc


def _categories(value: Any, field: str) -> list[str]:
    value = value or []
    if not isinstance(value, list) or len(value) > _CATEGORIES:
        raise errors.validation_error(field)
    found: list[str] = []
    for item in value:
        if not isinstance(item, str) or len(item.strip()) > _CATEGORY:
            raise errors.validation_error(field)
        code = item.strip().upper()
        if code and code not in found:
            found.append(code)
    return found


def parse(value: Any) -> list[dict[str, Any]]:
    """The list of documents sent by the form of the profile, checked."""
    value = value or []
    if not isinstance(value, list) or len(value) > _MAX:
        raise errors.validation_error("documents")
    result = []
    for index, raw in enumerate(value):
        where = f"documents[{index}]"
        if not isinstance(raw, dict):
            raise errors.validation_error(where)
        kind = raw.get("type")
        if kind not in TYPES:
            raise errors.validation_error(f"{where}.type", code="VALUE_UNKNOWN")
        doc = {
            "type": kind,
            "title": _text(raw.get("title"), f"{where}.title", required=kind == "other")
            if kind == "other"
            else "",
            "number": _text(raw.get("number"), f"{where}.number", required=True),
            "issued_on": _date(raw.get("issued_on"), f"{where}.issued_on"),
            "expires_on": _date(raw.get("expires_on"), f"{where}.expires_on"),
            "issued_by": _text(raw.get("issued_by"), f"{where}.issued_by"),
            "categories": _categories(raw.get("categories"), f"{where}.categories")
            if kind == LICENCE
            else [],
        }
        if doc["issued_on"] and doc["expires_on"] and doc["expires_on"] < doc["issued_on"]:
            raise errors.validation_error(f"{where}.expires_on", code="DATE_BEFORE_ISSUE")
        result.append(doc)
    return result


def shareable(documents: list[dict[str, Any]]) -> dict[str, Any]:
    """What a person may give a company that asks to link a card, in the shape of the card: the
    first passport or ID card as the identity document, the first driving licence as the licence,
    the documents of the kind «other» as other documents (PROPOSAL)."""
    identity = next((d for d in documents if d.get("type") in IDENTITY), None)
    licence = next((d for d in documents if d.get("type") == LICENCE), None)
    others = [d for d in documents if d.get("type") == "other"]
    return {
        "document": {
            "type": identity["type"],
            "number": identity["number"],
            "issued_on": identity.get("issued_on"),
            "expires_on": identity.get("expires_on"),
            "issued_by": identity.get("issued_by", ""),
        }
        if identity
        else None,
        "driver_license": {
            "number": licence["number"],
            "issued_on": licence.get("issued_on"),
            "expires_on": licence.get("expires_on"),
            "issued_by": licence.get("issued_by", ""),
            "categories": licence.get("categories", []),
        }
        if licence
        else None,
        "other_documents": [
            {
                "title": d.get("title", ""),
                "number": d["number"],
                "issued_on": d.get("issued_on"),
                "expires_on": d.get("expires_on"),
                "issued_by": d.get("issued_by", ""),
            }
            for d in others
        ]
        or None,
    }
