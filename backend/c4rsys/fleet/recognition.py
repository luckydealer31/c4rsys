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
from datetime import date
from typing import Any
from uuid import UUID

from django.conf import settings
from django.utils import timezone

from c4rsys.fleet import characteristics
from c4rsys.fleet.models import PaperReading, Vehicle
from c4rsys.platform import errors

TYPES = characteristics.VEHICLE_TYPES[Vehicle.TransportClass.GROUND]
# The fields with a list of values, and the lists.
CHOICES: dict[str, list[str]] = {
    "vehicle_type": TYPES,
    "body_type": characteristics.ALL_BODY_TYPES,
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
    # A person's name apart, so that a paper can decline it (customer's remark of 25.11.2026).
    "owner_last_name": 100,
    "owner_first_name": 100,
    "owner_middle_name": 100,
    # The province or the region of the plate, kept apart from it (customer's decision of
    # 23.11.2026).
    "plate_region": 60,
    # What a registration paper names besides (customer's decision of 23.11.2026), as written.
    "vehicle_category": 40,
    "eco_class": 40,
    "type_approval": 100,
    "special_marks": 500,
    # The address of the owner in one line, and what the documents of the card are made of: the
    # paper itself and the vehicle passport it names (customer's decisions of 23.11.2026).
    "owner_address": 500,
    "paper_name": 200,
    "paper_number": 150,
    "paper_issued_by": 200,
    "passport_number": 150,
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
    "the plate as written but without the province or the region it names, that province or "
    "region apart as written (the digits of a region inside a plate, as on a Russian one, stay "
    "in the plate), the country that issued the plate, the VIN, the numbers of the body, "
    "the chassis, the frame and the engine, the colour in English, and the characteristics. "
    "Leave out whatever cannot be read with confidence: do not guess, and do not work out a "
    "value from another one: give an empty text instead. "
    "Read the owner too: the name as written, and whether the owner is "
    "a person or an organization; of a person also the last name, the first name and the middle "
    "name (a patronymic) apart, as written, where the paper tells them apart. "
    "Read as written, where the paper has them: the power in "
    "kilowatts and in horsepower, the category of the vehicle, the ecological class, the "
    "approval of the type and the special marks. Read the address of the owner in one line. "
    "Read about the paper itself: its name as printed, its series and number, the date and the "
    "authority of its issue; and the series and the number of the vehicle passport if the paper "
    "names it. Other people named in the document are not "
    "asked for. The type «motorcycle» is any two-wheeler, a moped and a scooter too. The body "
    "is one of the type: "
    + "; ".join(f"{k} — {', '.join(v)}" for k, v in characteristics.BODY_TYPES.items())
    + "; another type has no body."
)


def _field(description: str) -> dict[str, Any]:
    """Every field is a text, and an empty one means "not read": the outer service takes no more
    than 16 fields that may be of two types, and a paper has more."""
    return {"type": "string", "description": description + "; an empty text if not read"}


SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "year",
        "plate_country",
        "power_kw",
        "paper_issued_on",
        *CHOICES,
        *TEXTS,
        *NUMBERS,
    ],
    "properties": {
        "year": _field("the year of manufacture in digits"),
        "plate_country": _field("ISO 3166-1 alpha-2"),
        **{k: _field("one of: " + ", ".join(v)) for k, v in CHOICES.items()},
        **{k: _field(f"up to {v} characters") for k, v in TEXTS.items()},
        **{k: _field("a positive number in digits, as written") for k in NUMBERS},
        "power_kw": _field("the power in kilowatts as written, a number"),
        "paper_issued_on": _field("the date the paper was issued, YYYY-MM-DD"),
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
    # «110.1» of a paper is 110: the characteristics are whole numbers.
    if isinstance(value, str) and re.fullmatch(r"\d{1,9}([.,]\d{1,2})?", value.strip()):
        value = round(float(value.strip().replace(",", ".")))
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
    # The power in kilowatts is a number with one digit after the point at most.
    kw = _text(answer.get("power_kw"), 12).replace(",", ".")
    texts["power_kw"] = kw if re.fullmatch(r"[1-9]\d{0,3}(\.\d)?", kw) else ""
    try:
        texts["paper_issued_on"] = date.fromisoformat(
            _text(answer.get("paper_issued_on"), 10)
        ).isoformat()
    except ValueError:
        texts["paper_issued_on"] = ""
    numbers["year"] = _whole(answer.get("year"), 1900, timezone.localdate().year + 1)
    return Paper(texts=_fitted(texts), numbers=numbers)


def _fitted(texts: dict[str, str]) -> dict[str, str]:
    """A body belongs to the type (decision of 23.11.2026); a reading kept before it may name
    the former type «moped»."""
    if texts.get("vehicle_type") == "moped":
        texts["vehicle_type"] = "motorcycle"
    allowed = characteristics.BODY_TYPES.get(texts.get("vehicle_type", ""), [])
    if texts.get("body_type") and texts["body_type"] not in allowed:
        texts["body_type"] = ""
    return texts


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


def read_kept(file_id: UUID, content: bytes, content_type: str) -> Paper:
    """A paper is read by the outer service once (customer's remark of 22.11.2026): the reading
    is kept with the file, and whoever asks for the same file again — the site after the bot —
    gets it without another request to the service."""
    kept = PaperReading.objects.filter(pk=file_id).first()
    if kept is not None:
        return Paper(texts=_fitted(dict(kept.texts)), numbers=dict(kept.numbers))
    paper = read(content, content_type)
    PaperReading.objects.update_or_create(
        pk=file_id, defaults={"texts": paper.texts, "numbers": paper.numbers}
    )
    return paper
