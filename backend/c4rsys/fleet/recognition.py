"""Reading a paper of a vehicle — a registration certificate, a blue book or another document
with its data — to fill the card of a new vehicle (customer's decision of 22.11.2026), as the
documents of suppliers are read into a receipt (warehouse/recognition.py).

The reading is done by an outer service: this module is the socket for it. A service is a
function registered under a name with `register`; the one named by `C4R_INVOICE_RECOGNITION` is
used — the same outer service reads both kinds of documents — and without it the feature is off.
The service is given `INSTRUCTION` and must answer with the JSON described by `SCHEMA`; `parse`
checks that answer, so no service can put an unchecked value into the system.

Nothing is written here: the result fills the form of a new vehicle, and the employee checks it
before the vehicle is made."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.utils import timezone

from c4rsys.fleet import characteristics
from c4rsys.fleet.models import Vehicle
from c4rsys.platform import errors

TYPES = characteristics.VEHICLE_TYPES[Vehicle.TransportClass.GROUND]
# The fields with a list of values, and the lists.
CHOICES: dict[str, list[str]] = {
    "vehicle_type": TYPES,
    "body_type": characteristics.BODY_TYPES,
    "fuel_type": characteristics.FUEL_TYPES,
    "transmission": characteristics.TRANSMISSIONS,
    "drive": characteristics.DRIVES,
    "owner_kind": ["person", "organization"],
}
# Free texts and their longest length.
TEXTS: dict[str, int] = {
    "make": 100,
    "model": 100,
    "plate": 20,
    "vin": 17,
    "body_number": 40,
    "chassis_number": 40,
    "frame_number": 40,
    "engine_number": 40,
    "color": 40,
    # The owner named in the paper (customer's decision of 22.11.2026).
    "owner_name": 200,
}
# Whole numbers and their largest value.
NUMBERS: dict[str, int] = {
    "engine_volume_cc": 99999,
    "power_hp": 5000,
    "seats": 200,
    "doors": 10,
    "curb_weight_kg": 200000,
    "gross_weight_kg": 200000,
}

INSTRUCTION = (
    "The document is a paper of a vehicle: a registration certificate, a registration book "
    "(such as the Thai blue or green book), a title or a similar document. It may be in "
    "Russian, Thai or English, printed or handwritten. Read the data of the vehicle itself: its "
    "make and model in Latin letters as the manufacturer writes them, the year of manufacture, "
    "the plate as written, the country that issued the plate, the VIN, the numbers of the body, "
    "the chassis, the frame and the engine, the colour in English, and the characteristics. "
    "Leave out whatever cannot be read with confidence: do not guess, and do not work out a "
    "value from another one. Read the owner too: the name as written, and whether the owner is "
    "a person or an organization. Other people named in the document are not asked for."
)


def _nullable(kind: str, description: str) -> dict[str, Any]:
    return {"type": [kind, "null"], "description": description}


SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["year", "plate_country", *CHOICES, *TEXTS, *NUMBERS],
    "properties": {
        "year": _nullable("integer", "the year of manufacture"),
        "plate_country": _nullable("string", "ISO 3166-1 alpha-2"),
        **{k: _nullable("string", "one of: " + ", ".join(v)) for k, v in CHOICES.items()},
        **{k: _nullable("string", f"up to {v} characters") for k, v in TEXTS.items()},
        **{k: _nullable("integer", "a positive whole number") for k in NUMBERS},
    },
}


@dataclass(frozen=True)
class Paper:
    """What was read; an empty text or `None` — not read."""

    texts: dict[str, str]
    numbers: dict[str, int | None]


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


def _whole(value: Any, low: int, high: int) -> int | None:
    """A whole number within the bounds; anything else is as good as not read."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and re.fullmatch(r"\d{1,9}", value.strip()):
        value = int(value.strip())
    return value if isinstance(value, int) and low <= value <= high else None


def parse(answer: Any) -> Paper:
    """The answer of a service, checked: a wrong value is dropped."""
    if not isinstance(answer, dict):
        raise errors.business_rule("RECOGNITION_FAILED")
    texts = {key: _text(answer.get(key), limit) for key, limit in TEXTS.items()}
    # A VIN is often printed in groups: the spaces are not a part of it.
    texts["vin"] = _text(answer.get("vin"), 40).replace(" ", "").upper()[: TEXTS["vin"]]
    for key, allowed in CHOICES.items():
        value = _text(answer.get(key), 40).lower()
        texts[key] = value if value in allowed else ""
    country = _text(answer.get("plate_country"), 2).upper()
    texts["plate_country"] = country if len(country) == 2 and country.isalpha() else ""
    numbers = {key: _whole(answer.get(key), 1, high) for key, high in NUMBERS.items()}
    numbers["year"] = _whole(answer.get("year"), 1900, timezone.localdate().year + 1)
    return Paper(texts=texts, numbers=numbers)


def read(content: bytes, content_type: str) -> Paper:
    if not configured():
        raise errors.business_rule("RECOGNITION_NOT_CONFIGURED")
    try:
        answer = _recognizers[settings.C4R_INVOICE_RECOGNITION](content, content_type)
    except errors.ApiError:
        raise
    except Exception as exc:  # noqa: BLE001 -- whatever the outer service fails with
        raise errors.business_rule("RECOGNITION_FAILED") from exc
    return parse(answer)
