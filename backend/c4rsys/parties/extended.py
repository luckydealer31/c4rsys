"""The extended data of client cards and counterparties (ТЗ § 6.2.12–6.2.13, § 6.1.13).

None of them is required at creation (the minimum is § 6.2.7–6.2.8); a document checks the data
it needs. Person: an identity document (a passport or an ID), a driving licence with the allowed
categories, the driving experience, other documents, the addresses of registration and of
residence. Company: the country of registration, the legal form, separate registration and tax
numbers, the legal and the actual address, the head, the contact person, the bank details, the
signing representative and the basis of their authority.

PROPOSAL: the shape of the documents (type, number, country, issue and expiry dates; the licence
with its categories), the experience as the date it is counted from, the bank details as free
text. The same set is used for clients and for counterparties (§ 6.1.13).
"""

from datetime import date
from typing import Any

from django.utils import timezone

from c4rsys.platform import errors

PERSON_TEXTS = {"registration_address": 500, "residence_address": 500}
COMPANY_TEXTS = {
    "legal_form": 100,
    "registration_number": 100,
    "tax_number": 100,
    "legal_address": 500,
    "actual_address": 500,
    "director_name": 200,
    "contact_person": 200,
    "bank_details": 2000,
    "signatory_name": 200,
    "signatory_basis": 500,
}
PERSON_FIELDS = (
    "id_document",
    "driver_license",
    "driving_since",
    "other_documents",
    *PERSON_TEXTS,
)
COMPANY_FIELDS = ("country", *COMPANY_TEXTS)
FIELDS = (*PERSON_FIELDS, *COMPANY_FIELDS)

# The identity document of a person (§ 6.2.12): a passport or an ID; «other» — PROPOSAL.
ID_DOCUMENT_TYPES = ("passport", "national_id", "other")
_DOC_TEXT = 100
_CATEGORY_MAX = 10
_CATEGORIES_MAX = 20
_OTHER_MAX = 20


def _empty(value: Any) -> bool:
    return value in (None, "", [], {})


def _text(value: Any, field: str, max_length: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise errors.validation_error(field)
    text = value.strip()
    if len(text) > max_length:
        raise errors.validation_error(field, code="TOO_LONG", max_length=max_length)
    return text


def _date(value: Any, field: str) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise errors.validation_error(field)
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise errors.validation_error(field, code="DATE_INVALID") from exc


def _period(doc: dict[str, Any], field: str) -> None:
    issued, expires = doc.get("issued_on"), doc.get("expires_on")
    if issued and issued > timezone.localdate().isoformat():
        raise errors.validation_error(f"{field}.issued_on", code="DATE_IN_FUTURE")
    if issued and expires and expires < issued:
        raise errors.validation_error(f"{field}.expires_on", code="PERIOD_INVALID")


def _object(value: Any, field: str, allowed: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise errors.validation_error(field)
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise errors.validation_error(f"{field}.{unknown[0]}", code="VALUE_UNKNOWN")
    return value


def _id_document(value: Any, field: str = "id_document") -> dict[str, Any] | None:
    if _empty(value):
        return None
    raw = _object(value, field, ("type", "number", "country", "issued_on", "expires_on"))
    doc = {
        "type": _text(raw.get("type"), f"{field}.type", 20),
        "number": _text(raw.get("number"), f"{field}.number", _DOC_TEXT),
        "country": _country(raw.get("country"), f"{field}.country"),
        "issued_on": _date(raw.get("issued_on"), f"{field}.issued_on"),
        "expires_on": _date(raw.get("expires_on"), f"{field}.expires_on"),
    }
    if doc["type"] not in ID_DOCUMENT_TYPES:
        raise errors.validation_error(f"{field}.type", code="VALUE_UNKNOWN")
    if not doc["number"]:
        raise errors.validation_error(f"{field}.number", code="FIELD_REQUIRED")
    _period(doc, field)
    return doc


def _license(value: Any, field: str = "driver_license") -> dict[str, Any] | None:
    if _empty(value):
        return None
    raw = _object(value, field, ("number", "country", "issued_on", "expires_on", "categories"))
    categories = raw.get("categories") or []
    if not isinstance(categories, list) or len(categories) > _CATEGORIES_MAX:
        raise errors.validation_error(f"{field}.categories")
    cleaned: list[str] = []
    for item in categories:
        code = _text(item, f"{field}.categories", _CATEGORY_MAX).upper()
        if code and code not in cleaned:
            cleaned.append(code)
    doc = {
        "number": _text(raw.get("number"), f"{field}.number", _DOC_TEXT),
        "country": _country(raw.get("country"), f"{field}.country"),
        "issued_on": _date(raw.get("issued_on"), f"{field}.issued_on"),
        "expires_on": _date(raw.get("expires_on"), f"{field}.expires_on"),
        "categories": cleaned,
    }
    if not doc["number"]:
        raise errors.validation_error(f"{field}.number", code="FIELD_REQUIRED")
    _period(doc, field)
    return doc


def _others(value: Any, field: str = "other_documents") -> list[dict[str, Any]]:
    if _empty(value):
        return []
    if not isinstance(value, list) or len(value) > _OTHER_MAX:
        raise errors.validation_error(field)
    result = []
    for index, item in enumerate(value):
        where = f"{field}[{index}]"
        raw = _object(item, where, ("title", "number", "issued_on", "expires_on", "note"))
        doc = {
            "title": _text(raw.get("title"), f"{where}.title", _DOC_TEXT),
            "number": _text(raw.get("number"), f"{where}.number", _DOC_TEXT),
            "issued_on": _date(raw.get("issued_on"), f"{where}.issued_on"),
            "expires_on": _date(raw.get("expires_on"), f"{where}.expires_on"),
            "note": _text(raw.get("note"), f"{where}.note", 500),
        }
        if not doc["title"]:
            raise errors.validation_error(f"{where}.title", code="FIELD_REQUIRED")
        _period(doc, where)
        result.append(doc)
    return result


def _country(value: Any, field: str) -> str:
    text = _text(value, field, 2).upper()
    if text and (len(text) != 2 or not text.isalpha()):
        raise errors.validation_error(field, code="COUNTRY_INVALID")
    return text


def _since(value: Any) -> date | None:
    found = _date(value, "driving_since")
    if found is None:
        return None
    day = date.fromisoformat(found)
    if day > timezone.localdate():
        raise errors.validation_error("driving_since", code="DATE_IN_FUTURE")
    return day


def parse_value(name: str, value: Any) -> Any:
    if name == "id_document":
        return _id_document(value)
    if name == "driver_license":
        return _license(value)
    if name == "driving_since":
        return _since(value)
    if name == "other_documents":
        return _others(value)
    if name == "country":
        return _country(value, name)
    limits = {**PERSON_TEXTS, **COMPANY_TEXTS}
    return _text(value, name, limits[name])


def parse(data: dict[str, Any], kind: str, *, only_given: bool) -> dict[str, Any]:
    """The extended fields of the body. A field of the other kind is refused when it is filled
    (a person card has no legal form, a company card no driving licence)."""
    result: dict[str, Any] = {}
    applicable = PERSON_FIELDS if kind == "person" else COMPANY_FIELDS
    for name in FIELDS:
        if name not in data:
            if not only_given and name in applicable:
                result[name] = parse_value(name, None)
            continue
        value = parse_value(name, data[name])
        if name not in applicable:
            if not _empty(value):
                raise errors.validation_error(name, code="FIELD_NOT_APPLICABLE")
            continue
        result[name] = value
    return result


def values_of(card: Any) -> dict[str, Any]:
    """The extended data of a card as JSON."""
    return {
        name: (
            getattr(card, name).isoformat()
            if name == "driving_since" and getattr(card, name)
            else getattr(card, name)
        )
        for name in FIELDS
    }


def as_payload(changes: dict[str, Any]) -> dict[str, Any]:
    return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in changes.items()}
