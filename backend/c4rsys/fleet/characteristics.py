"""Identification data and characteristics of a vehicle (ТЗ § 8.1.8–8.1.12, § 8.6.1).

All of them are optional; only the name of the card is required, and it may be formed from the
make, the model and the plate (§ 8.1.10). The lists of types, bodies, fuels, gearboxes and
drives are a PROPOSAL of the agent (24.10.2026) until the customer confirms them.
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from c4rsys.fleet.models import Vehicle, VehicleMake, VehicleModel
from c4rsys.platform import errors

# Ground vehicles first (§ 8.1.8); air and water come later.
AVAILABLE_CLASSES = {Vehicle.TransportClass.GROUND}
VEHICLE_TYPES = {
    Vehicle.TransportClass.GROUND: [
        "passenger_car",
        "truck",
        "bus",
        "motorcycle",
        "moped",
        "other",
    ],
}
BODY_TYPES = [
    "sedan",
    "hatchback",
    "wagon",
    "suv",
    "crossover",
    "coupe",
    "convertible",
    "minivan",
    "pickup",
    "van",
    "other",
]
FUEL_TYPES = ["petrol", "diesel", "hybrid", "plug_in_hybrid", "electric", "gas", "other"]
TRANSMISSIONS = ["manual", "automatic", "robot", "cvt"]
DRIVES = ["fwd", "rwd", "awd"]

# § 8.6.1: VIN is upper-cased and trimmed; similar symbols are not replaced. 17 characters
# without I, O, Q is the usual format — a different one is only a warning.
VIN_FORMAT = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
COUNTRY = re.compile(r"^[A-Z]{2}$")
# Numbers of the body, chassis and frame: upper-cased and trimmed like the VIN (§ 8.6.1).
NUMBERS = ("body_number", "chassis_number", "frame_number")


def _text(data: dict[str, Any], key: str, max_length: int) -> str:
    value = data.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise errors.validation_error(key)
    value = value.strip()
    if len(value) > max_length:
        raise errors.validation_error(key, code="FIELD_TOO_LONG", max_length=max_length)
    return value


def _choice(data: dict[str, Any], key: str, allowed: list[str]) -> str:
    value = _text(data, key, 30)
    if value and value not in allowed:
        raise errors.validation_error(key)
    return value


def _int(data: dict[str, Any], key: str, low: int, high: int) -> int | None:
    value = data.get(key)
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise errors.validation_error(key, code="OUT_OF_RANGE", min=low, max=high)
    return value


def _decimal(data: dict[str, Any], key: str) -> Decimal | None:
    value = data.get(key)
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise errors.validation_error(key) from exc
    if not Decimal("0") < number <= Decimal("99.9"):
        raise errors.validation_error(key, code="OUT_OF_RANGE", min=0, max=99.9)
    return number.quantize(Decimal("0.1"))


def normalize_plate(number: str) -> str:
    """For comparison only: letters upper-cased, spaces and dashes removed."""
    return re.sub(r"[\s\-]", "", number).upper()


@dataclass(frozen=True)
class Characteristics:
    transport_class: str = Vehicle.TransportClass.GROUND
    vehicle_type: str = ""
    make_code: str = ""
    make_other: str = ""
    model_code: str = ""
    model_other: str = ""
    year: int | None = None
    vin: str = ""
    body_number: str = ""
    chassis_number: str = ""
    frame_number: str = ""
    engine_number: str = ""
    plate: str = ""
    plate_country: str = ""
    # The region or the registration authority of the plate, where it applies (§ 8.6.1).
    plate_region: str = ""
    color: str = ""
    body_type: str = ""
    fuel_type: str = ""
    transmission: str = ""
    drive: str = ""
    engine_volume_l: Decimal | None = None
    power_hp: int | None = None
    seats: int | None = None
    doors: int | None = None
    warnings: list[str] = field(default_factory=list)
    # The identifiers as they were entered, before the normalization (§ 8.6.1).
    originals: dict[str, str] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        """For the stored request of the command (A07)."""
        body = {k: v for k, v in self.__dict__.items() if k not in ("warnings", "originals")}
        body["engine_volume_l"] = str(self.engine_volume_l) if self.engine_volume_l else None
        return body


def parse(data: dict[str, Any], this_year: int) -> Characteristics:
    transport_class = _text(data, "transport_class", 10) or Vehicle.TransportClass.GROUND
    if transport_class not in AVAILABLE_CLASSES:
        raise errors.validation_error("transport_class", code="TRANSPORT_CLASS_NOT_AVAILABLE")
    vehicle_type = _choice(data, "vehicle_type", VEHICLE_TYPES[Vehicle.TransportClass.GROUND])
    make_code = _text(data, "make", 60)
    model_code = _text(data, "model", 120)
    if (
        make_code
        and not VehicleMake.objects.filter(code=make_code, vehicle_type=vehicle_type).exists()
    ):
        raise errors.validation_error("make", code="MAKE_UNKNOWN")
    if model_code and not VehicleModel.objects.filter(code=model_code, make_id=make_code).exists():
        raise errors.validation_error("model", code="MODEL_UNKNOWN")
    vin = _text(data, "vin", 40).upper()
    plate_country = _text(data, "plate_country", 2).upper()
    if plate_country and not COUNTRY.match(plate_country):
        raise errors.validation_error("plate_country", code="COUNTRY_INVALID")
    result = Characteristics(
        transport_class=transport_class,
        vehicle_type=vehicle_type,
        make_code=make_code,
        make_other="" if make_code else _text(data, "make_other", 100),
        model_code=model_code,
        model_other="" if model_code else _text(data, "model_other", 150),
        year=_int(data, "year", 1886, this_year + 1),
        vin=vin,
        body_number=_text(data, "body_number", 40).upper(),
        chassis_number=_text(data, "chassis_number", 40).upper(),
        frame_number=_text(data, "frame_number", 40).upper(),
        engine_number=_text(data, "engine_number", 40).upper(),
        plate=_text(data, "plate", 20),
        plate_country=plate_country,
        plate_region=_text(data, "plate_region", 60),
        color=_text(data, "color", 60),
        body_type=_choice(data, "body_type", BODY_TYPES),
        fuel_type=_choice(data, "fuel_type", FUEL_TYPES),
        transmission=_choice(data, "transmission", TRANSMISSIONS),
        drive=_choice(data, "drive", DRIVES),
        engine_volume_l=_decimal(data, "engine_volume_l"),
        power_hp=_int(data, "power_hp", 1, 5000),
        seats=_int(data, "seats", 1, 200),
        doors=_int(data, "doors", 0, 10),
    )
    if vin and not VIN_FORMAT.match(vin):
        result.warnings.append("VIN_FORMAT")
    for key in ("vin", *NUMBERS):
        raw = data.get(key)
        if isinstance(raw, str) and raw.strip():
            result.originals[key] = raw[:60]
    return result


def make_name(value: Characteristics) -> str:
    """Name from the make, the model and the plate — whatever of them is given (§ 8.1.10)."""
    make = (
        VehicleMake.objects.filter(code=value.make_code).values_list("name", flat=True).first()
        if value.make_code
        else value.make_other
    )
    model = (
        VehicleModel.objects.filter(code=value.model_code).values_list("name", flat=True).first()
        if value.model_code
        else value.model_other
    )
    return " ".join(part for part in (make, model, value.plate) if part)


def apply(vehicle: Vehicle, value: Characteristics) -> None:
    vehicle.transport_class = value.transport_class
    vehicle.vehicle_type = value.vehicle_type
    vehicle.make_id = value.make_code or None
    vehicle.make_other = value.make_other
    vehicle.model_id = value.model_code or None
    vehicle.model_other = value.model_other
    for key in (
        "year",
        "vin",
        *NUMBERS,
        "engine_number",
        "color",
        "body_type",
        "fuel_type",
        "transmission",
        "drive",
        "engine_volume_l",
        "power_hp",
        "seats",
        "doors",
    ):
        setattr(vehicle, key, getattr(value, key))


def matches(vehicle: Vehicle) -> list[str]:
    """Identifiers that coincide with another global entity (§ 8.6.1, § 8.6.3): a reason to
    check, not proof of one vehicle. Only the fact is told, not whose the other record is."""
    from c4rsys.fleet import identity

    return identity.matches(vehicle)


def describe(vehicle: Vehicle) -> dict[str, Any]:
    plate = vehicle.plates.filter(valid_to__isnull=True).first()
    return {
        "transport_class": vehicle.transport_class,
        "vehicle_type": vehicle.vehicle_type,
        "make": vehicle.make_id,
        "make_name": vehicle.make.name if vehicle.make_id and vehicle.make else vehicle.make_other,
        "model": vehicle.model_id,
        "model_name": (
            vehicle.model.name if vehicle.model_id and vehicle.model else vehicle.model_other
        ),
        "year": vehicle.year,
        "vin": vehicle.vin,
        "body_number": vehicle.body_number,
        "chassis_number": vehicle.chassis_number,
        "frame_number": vehicle.frame_number,
        "engine_number": vehicle.engine_number,
        "plate": plate.number if plate else "",
        "plate_country": plate.country if plate else "",
        "plate_region": plate.region if plate else "",
        "color": vehicle.color,
        "body_type": vehicle.body_type,
        "fuel_type": vehicle.fuel_type,
        "transmission": vehicle.transmission,
        "drive": vehicle.drive,
        "engine_volume_l": str(vehicle.engine_volume_l) if vehicle.engine_volume_l else None,
        "power_hp": vehicle.power_hp,
        "seats": vehicle.seats,
        "doors": vehicle.doors,
        "identifier_matches": matches(vehicle),
    }


# --- Values of single characteristics (§ 8.2.1, § 8.6.2) ------------------------------------

# The characteristics a working value of a card can hold: the keys of the API.
FIELDS = (
    "vehicle_type",
    "make",
    "model",
    "year",
    "vin",
    "body_number",
    "chassis_number",
    "frame_number",
    "engine_number",
    "color",
    "body_type",
    "fuel_type",
    "transmission",
    "drive",
    "engine_volume_l",
    "power_hp",
    "seats",
    "doors",
)
# The basic factory characteristics that the c4rsys staff may confirm (§ 8.2.1). PROPOSAL: the
# ТЗ does not list them; the colour (it may be repainted) and the plate (periods, § 8.2.11) are
# not among them. TODO(spec): the list of the basic factory characteristics.
FACTORY_FIELDS = tuple(f for f in FIELDS if f != "color")


def value_of(vehicle: Vehicle, name: str) -> Any:
    """A comparable JSON value of one characteristic of the global vehicle."""
    if name == "make":
        return {"code": vehicle.make_id or "", "other": vehicle.make_other}
    if name == "model":
        return {"code": vehicle.model_id or "", "other": vehicle.model_other}
    if name == "engine_volume_l":
        return str(vehicle.engine_volume_l) if vehicle.engine_volume_l is not None else None
    return getattr(vehicle, name)


def value_in(details: Characteristics, name: str) -> Any:
    """The same value as entered (parsed) in a form."""
    if name == "make":
        return {"code": details.make_code, "other": details.make_other}
    if name == "model":
        return {"code": details.model_code, "other": details.model_other}
    if name == "engine_volume_l":
        return str(details.engine_volume_l) if details.engine_volume_l is not None else None
    return getattr(details, name)


def is_empty(value: Any) -> bool:
    if isinstance(value, dict):
        return not value.get("code") and not value.get("other")
    return value in (None, "")


def set_value(vehicle: Vehicle, name: str, value: Any) -> None:
    if name == "make":
        vehicle.make_id = value.get("code") or None
        vehicle.make_other = "" if value.get("code") else value.get("other", "")
    elif name == "model":
        vehicle.model_id = value.get("code") or None
        vehicle.model_other = "" if value.get("code") else value.get("other", "")
    elif name == "engine_volume_l":
        vehicle.engine_volume_l = Decimal(value) if value not in (None, "") else None
    else:
        setattr(vehicle, name, value)


def input_of(name: str, value: Any) -> dict[str, Any]:
    """The value back as input of `parse`."""
    if name in ("make", "model"):
        value = value or {}
        return {name: value.get("code", ""), f"{name}_other": value.get("other", "")}
    return {name: value}


def display(name: str, value: Any) -> str:
    """A text of a value for the interface."""
    if name in ("make", "model") and isinstance(value, dict):
        code = value.get("code")
        if not code:
            return str(value.get("other", ""))
        table = VehicleMake if name == "make" else VehicleModel
        found = table.objects.filter(code=code).values_list("name", flat=True).first()
        return str(found or code)
    return "" if value is None else str(value)


def parse_one(name: str, data: dict[str, Any], vehicle: Vehicle, this_year: int) -> Any:
    """One characteristic given by `data`, checked as in the full form: the other values are
    taken from the vehicle, so a model is checked against the make."""
    if name not in FIELDS:
        raise errors.validation_error("field")
    base: dict[str, Any] = {"transport_class": vehicle.transport_class}
    for other in FIELDS:
        base.update(input_of(other, value_of(vehicle, other)))
    if name in ("make", "model"):
        base.update({k: data.get(k, "") for k in (name, f"{name}_other")})
        if name == "make":
            base.update({"model": "", "model_other": ""})
    else:
        base[name] = data.get(name)
    return value_in(parse(base, this_year), name)
