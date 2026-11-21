"""Reading a delivery note, a receipt or another document of a supplier (customer's decision of
20.11.2026): the supplier, the date, the number and the lines with their articles, quantities
and prices, for a warehouse receipt to be filled from them.

The reading itself is done by an outer service: this module is the socket for it, and the first
service is in recognition_anthropic.py (customer's decision of 21.11.2026). A service is a
function registered under a name with `register`;
`C4R_INVOICE_RECOGNITION` names the one in use, empty — the feature is off. Whatever the service
is, it is given `INSTRUCTION` and must answer with the JSON described by `SCHEMA`; `parse` checks
that answer, so no service can put an unchecked value into the system.

Nothing is written here: the result is an offer the employee accepts line by line."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from django.conf import settings

from c4rsys.platform import errors

# What a service is asked for. The languages and the handwriting are the customer's decision.
INSTRUCTION = (
    "The document is a delivery note, an invoice or a receipt of a supplier of spare parts and "
    "materials. It may be in Russian, Thai or English, printed or handwritten. Read the "
    "supplier, the date and the number of the document, the currency and every line of goods: "
    "its name as written, its article (part number), its quantity and its price for one unit. "
    "Leave out whatever cannot be read with confidence: do not guess. Totals, taxes, delivery "
    "and discounts are not lines of goods."
)
SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["supplier_name", "document_date", "document_number", "currency", "lines"],
    "properties": {
        "supplier_name": {"type": ["string", "null"]},
        "document_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        "document_number": {"type": ["string", "null"]},
        "currency": {"type": ["string", "null"], "description": "ISO 4217"},
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "article", "quantity", "unit_cost"],
                "properties": {
                    "name": {"type": "string"},
                    "article": {"type": ["string", "null"]},
                    "quantity": {"type": ["string", "null"], "description": "a decimal number"},
                    "unit_cost": {"type": ["string", "null"], "description": "a decimal number"},
                },
            },
        },
    },
}
MAX_LINES = 200
MAX_NUMBER = Decimal("10000000000")


@dataclass(frozen=True)
class Line:
    name: str
    article: str
    quantity: Decimal | None
    unit_cost: Decimal | None


@dataclass(frozen=True)
class Document:
    supplier_name: str
    document_date: date | None
    document_number: str
    currency: str
    lines: tuple[Line, ...]


# A service: the content of the file and its type in, the answer by `SCHEMA` out.
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


def _number(value: Any) -> Decimal | None:
    """A positive decimal; anything else is as good as not read."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        number = Decimal(str(value).replace(" ", "").replace(",", "."))
    except InvalidOperation:
        return None
    return number if number.is_finite() and 0 < number < MAX_NUMBER else None


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def parse(answer: Any) -> Document:
    """The answer of a service, checked: a wrong value is dropped, a line without a name too."""
    if not isinstance(answer, dict) or not isinstance(answer.get("lines"), list):
        raise errors.business_rule("RECOGNITION_FAILED")
    lines = []
    for raw in answer["lines"][:MAX_LINES]:
        if not isinstance(raw, dict):
            continue
        name = _text(raw.get("name"), 200)
        if name:
            lines.append(
                Line(
                    name=name,
                    article=_text(raw.get("article"), 100),
                    quantity=_number(raw.get("quantity")),
                    unit_cost=_number(raw.get("unit_cost")),
                )
            )
    currency = _text(answer.get("currency"), 3).upper()
    return Document(
        supplier_name=_text(answer.get("supplier_name"), 200),
        document_date=_date(answer.get("document_date")),
        document_number=_text(answer.get("document_number"), 100),
        currency=currency if len(currency) == 3 and currency.isalpha() else "",
        lines=tuple(lines),
    )


def read(content: bytes, content_type: str) -> Document:
    if not configured():
        raise errors.business_rule("RECOGNITION_NOT_CONFIGURED")
    try:
        answer = _recognizers[settings.C4R_INVOICE_RECOGNITION](content, content_type)
    except errors.ApiError:
        raise
    except Exception as exc:  # noqa: BLE001 -- whatever the outer service fails with
        raise errors.business_rule("RECOGNITION_FAILED") from exc
    return parse(answer)
