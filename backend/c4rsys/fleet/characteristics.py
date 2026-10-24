"""Identification data and characteristics of a vehicle (ТЗ § 8.1.8–8.1.12, § 8.6.1).

All of them are optional; only the name of the card is required, and it may be formed from the
make, the model and the plate (§ 8.1.10). The lists of types, bodies, fuels, gearboxes and
drives are a PROPOSAL of the agent (24.10.2026) until the customer confirms them.
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from c4rsys.fleet.models import Vehicle, VehicleMake, VehicleModel, VehiclePlate
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
    plate: str = ""
    plate_country: str = ""
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

    def payload(self) -> dict[str, Any]:
        """For the stored request of the command (A07)."""
        body = {k: v for k, v in self.__dict__.items() if k != "warnings"}
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
        plate=_text(data, "plate", 20),
        plate_country=plate_country,
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
    """Identifiers that coincide with another vehicle (§ 8.6.1, § 8.6.3): a reason to check, not
    proof of one vehicle. Only the fact is told, not whose the other record is."""
    found = []
    if vehicle.vin and Vehicle.objects.filter(vin=vehicle.vin).exclude(pk=vehicle.pk).exists():
        found.append("vin")
    plate = vehicle.plates.filter(valid_to__isnull=True).first()
    if (
        plate is not None
        and VehiclePlate.objects.filter(
            number_normalized=plate.number_normalized,
            country=plate.country,
            valid_to__isnull=True,
        )
        .exclude(vehicle=vehicle)
        .exists()
    ):
        found.append("plate")
    return found


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
        "plate": plate.number if plate else "",
        "plate_country": plate.country if plate else "",
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
