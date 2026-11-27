"""Reading a document of a client, an investor or a counterparty — a passport, an ID, a driving
licence, a paper of the registration of a company — to fill a new card (customer's decision of
28.11.2026), as a paper of a vehicle is read into the card of a new vehicle
(fleet/recognition.py).

The reading is done by an outer service: this module is the socket for it, with the same rules —
the service registered under the name of `C4R_INVOICE_RECOGNITION` is used, it is given
`INSTRUCTION` and answers with the JSON of `SCHEMA`, and `parse` checks the answer.

Nothing is written here: the result fills the form of a new card, and the employee checks it
before the card is made."""

from collections.abc import Callable
from datetime import date
from typing import Any

from django.conf import settings
from django.utils import timezone

from c4rsys.parties import extended
from c4rsys.platform import errors

PARTY_KINDS = ["person", "company"]
DOCUMENT_KINDS = ["passport", "national_id", "driver_license", "company_registration", "other"]
# Free texts and their longest length.
TEXTS: dict[str, int] = {
    "last_name": 150,
    "first_name": 150,
    "middle_name": 150,
    "number": 100,
    "issued_by": 100,
    "categories": 100,
    "address": 500,
    "company_name": 200,
    "legal_form": 100,
    "registration_number": 100,
    "tax_number": 100,
    "director_name": 200,
}
DATES = ("birth_date", "issued_on", "expires_on")

INSTRUCTION = (
    "The document belongs to a person or to a company: a passport, a national ID card, a "
    "driving licence, or a paper of the registration of a company (a certificate of "
    "registration, an extract of a register, a tax registration). It may be in Russian, Thai, "
    "English or another language, printed or handwritten. Say whether it is of a person or of a "
    "company and which document it is. "
    "Of a person read the last name, the first name and the middle name (a patronymic) apart; "
    "where the document gives the name both in a local script and in Latin letters, take the "
    "Latin one; the date of birth; the number of the document with its series, as written; the "
    "country that issued it; the dates of its issue and of its expiry; the authority that "
    "issued it; the categories of a driving licence, separated by commas; the address of the "
    "registration or of the residence in one line, if the document names it. "
    "Of a company read its name as written, without the legal form where it stands apart; the "
    "legal form; the registration number; the tax number; the country of the registration; the "
    "legal address in one line; the name of the head. "
    "Dates are given in the Gregorian calendar: a year of the Buddhist era is turned into it. "
    "Leave out whatever cannot be read with confidence: do not guess, and do not work out a "
    "value from another one: give an empty text instead."
)


def _field(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description + "; an empty text if not read"}


SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["party_kind", "document_kind", "country", *DATES, *TEXTS],
    "properties": {
        "party_kind": _field("one of: " + ", ".join(PARTY_KINDS)),
        "document_kind": _field("one of: " + ", ".join(DOCUMENT_KINDS)),
        "country": _field("ISO 3166-1 alpha-2"),
        **{k: _field("YYYY-MM-DD") for k in DATES},
        **{k: _field(f"up to {v} characters") for k, v in TEXTS.items()},
    },
}

Recognizer = Callable[[bytes, str], dict[str, Any]]
_recognizers: dict[str, Recognizer] = {}


def register(name: str, recognizer: Recognizer) -> None:
    _recognizers[name] = recognizer


def configured() -> bool:
    return (
        bool(settings.C4R_INVOICE_RECOGNITION) and settings.C4R_INVOICE_RECOGNITION in _recognizers
    )


def _text(value: Any, limit: int) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _date(value: Any) -> str | None:
    try:
        return date.fromisoformat(_text(value, 10)).isoformat()
    except ValueError:
        return None


def parse(answer: Any) -> dict[str, Any]:
    """The answer of a service, checked, as the fields of a new card: a wrong value is dropped.
    `kind` is the kind of the card, `document_kind` — which document was read, `card` — the
    fields the creation of a card takes."""
    if not isinstance(answer, dict):
        raise errors.business_rule("RECOGNITION_FAILED")
    texts = {key: _text(answer.get(key), limit) for key, limit in TEXTS.items()}
    kind = _text(answer.get("party_kind"), 10).lower()
    document = _text(answer.get("document_kind"), 30).lower()
    if document not in DOCUMENT_KINDS:
        document = "other"
    if kind not in PARTY_KINDS:
        kind = "company" if document == "company_registration" else "person"
    country = _text(answer.get("country"), 10).upper()
    country = country if len(country) == 2 and country.isalpha() else ""
    today = timezone.localdate().isoformat()
    issued, expires = _date(answer.get("issued_on")), _date(answer.get("expires_on"))
    if issued and issued > today:
        issued = None
    if issued and expires and expires < issued:
        expires = None
    card: dict[str, Any] = {}
    if kind == "company":
        for key in ("company_name", "legal_form", "registration_number", "tax_number"):
            card[key] = texts[key]
        card["director_name"] = texts["director_name"]
        card["legal_address"] = texts["address"]
        card["country"] = country
    else:
        for key in ("last_name", "first_name", "middle_name"):
            card[key] = texts[key]
        birth = _date(answer.get("birth_date"))
        card["birth_date"] = birth if birth and birth <= today else None
        card["registration_address"] = texts["address"]
        paper = {
            "number": texts["number"],
            "country": country,
            "issued_on": issued,
            "expires_on": expires,
            "issued_by": texts["issued_by"],
        }
        if texts["number"] and document == "driver_license":
            categories = [c for c in texts["categories"].replace(";", ",").split(",") if c.strip()]
            card["driver_license"] = {**paper, "categories": [c.strip() for c in categories][:20]}
        elif texts["number"]:
            kinds = extended.ID_DOCUMENT_TYPES
            card["id_document"] = {**paper, "type": document if document in kinds else "other"}
        # What the creation of a card would refuse is as good as not read.
        for name in ("driver_license", "id_document"):
            if name in card:
                try:
                    card[name] = extended.parse_value(name, card[name])
                except errors.ApiError:
                    del card[name]
    return {"kind": kind, "document_kind": document, "card": card}


def read(content: bytes, content_type: str) -> dict[str, Any]:
    if not configured():
        raise errors.business_rule("RECOGNITION_NOT_CONFIGURED")
    try:
        answer = _recognizers[settings.C4R_INVOICE_RECOGNITION](content, content_type)
    except errors.ApiError:
        raise
    except Exception as exc:  # noqa: BLE001 -- whatever the outer service fails with
        raise errors.business_rule("RECOGNITION_FAILED") from exc
    return parse(answer)
