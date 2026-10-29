"""Import template of vehicles (ТЗ § 22.3.1, § 22.2.3).

Required: the ID of a location of the company, the relation «own» or «managed», and a name that
is given or can be formed from the make, the model or the plate (§ 8.1.10). The unit of the
odometer is required by the card itself (§ 8.2.9). Everything else is optional; the owner may be
left unconfirmed or not given. Matches are searched by the VHL ID, the VIN and the plate within
the company only; a coincidence with a vehicle of another company is not disclosed and is handled
by the rules of § 8.6. A row whose VHL ID points to one card and VIN or plate to another is a
conflict.

Not here yet (OPEN): numbers of the body, chassis and frame — the card has no such fields.
"""

from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from django.utils import timezone

from c4rsys.companies import access, locations
from c4rsys.companies.models import Company
from c4rsys.fleet import characteristics, owners, services
from c4rsys.fleet.models import VehicleCard, VehicleMake, VehicleModel, VehiclePlate
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.warehouse.imports import Checked, Scope

OWNER_COLUMNS = ("owner_kind", "owner_lookup", "owner_name", "owner_contact")
NUMBERS = ("year", "power_hp", "seats", "doors")


def _make(text: str, vehicle_type: str) -> tuple[str, str, str]:
    """(catalog code, free text, vehicle type) for a make written by its code or name; the type
    of the catalog make is taken when the row gives none."""
    if not text:
        return "", "", vehicle_type
    makes = VehicleMake.objects.all()
    if vehicle_type:
        makes = makes.filter(vehicle_type=vehicle_type)
    for make in (
        makes.filter(code__iexact=text)
        | makes.filter(name__iexact=text)
        | makes.filter(name_cyrillic__iexact=text)
    ):
        return make.code, "", make.vehicle_type
    return "", text, vehicle_type


def _model(text: str, make_code: str) -> tuple[str, str]:
    if not text:
        return "", ""
    if make_code:
        models = VehicleModel.objects.filter(make_id=make_code)
        for model in models.filter(code__iexact=text) | models.filter(name__iexact=text):
            return model.code, ""
    return "", text


def _name(values: dict[str, str], details: characteristics.Characteristics) -> str:
    """The own name, or one formed from the make, the model, the plate or the VIN (§ 22.3.1)."""
    return values.get("name", "") or characteristics.make_name(details) or details.vin


class VehiclesTemplate:
    kind = "vehicles"
    version = 1
    sheet = "vehicles"
    permission = "vehicle.create"
    object_type = "vehicle_card"
    columns: dict[str, int] = {
        "vehicle_id": 40,
        "location_id": 40,
        "relation": 10,
        "name": 200,
        "odometer_unit": 2,
        "vehicle_type": 30,
        "make": 100,
        "model": 150,
        "year": 4,
        "plate": 20,
        "plate_country": 2,
        "vin": 40,
        "color": 60,
        "body_type": 30,
        "fuel_type": 30,
        "transmission": 30,
        "drive": 30,
        "engine_volume_l": 5,
        "power_hp": 5,
        "seats": 3,
        "doors": 2,
        "odometer": 12,
        "contexts": 100,
        "owner_kind": 20,
        "owner_lookup": 254,
        "owner_name": 300,
        "owner_contact": 254,
    }
    required: tuple[str, ...] = ("location_id", "relation", "odometer_unit")
    help = {
        "vehicle_id": (
            "VHL ID of a vehicle already in your garage, to find it",
            "VHL ID ТС, уже имеющегося в гараже компании, — для поиска совпадения",
            "",
        ),
        "location_id": (
            "ID of a location of the company (sheet «locations»); required",
            "ID локации компании (лист «locations»); обязательно",
            "",
        ),
        "relation": (
            "own or managed; required",
            "own (владение) или managed (управление); обязательно",
            "own",
        ),
        "name": (
            "Own name of the card; formed from make, model and plate when empty",
            "Собственное название; если пусто — из марки, модели и госномера",
            "Camry white",
        ),
        "odometer_unit": (
            "Odometer unit: km or mi; required",
            "Единица пробега: km или mi; обязательно",
            "km",
        ),
        "vehicle_type": (
            "passenger_car, truck, bus, motorcycle, moped, other",
            "passenger_car, truck, bus, motorcycle, moped, other — вид транспорта",
            "passenger_car",
        ),
        "make": ("Make: code or name of the catalog, or any name", "Марка", "Toyota"),
        "model": ("Model", "Модель", "Camry"),
        "year": ("Year of manufacture", "Год выпуска", "2021"),
        "plate": ("Registration plate", "Госномер", "1กข 1234"),
        "plate_country": ("Country of the plate, two letters", "Страна госномера, две буквы", "TH"),
        "vin": ("VIN", "VIN", ""),
        "color": ("Color", "Цвет", "white"),
        "body_type": (
            "sedan, hatchback, wagon, suv, crossover, coupe, convertible, minivan, pickup, van,"
            " other",
            "Тип кузова: sedan, hatchback, wagon, suv, crossover, coupe, convertible, minivan,"
            " pickup, van, other",
            "sedan",
        ),
        "fuel_type": (
            "petrol, diesel, hybrid, plug_in_hybrid, electric, gas, other",
            "Топливо: petrol, diesel, hybrid, plug_in_hybrid, electric, gas, other",
            "petrol",
        ),
        "transmission": ("manual, automatic, robot, cvt", "КПП: manual, automatic, robot, cvt", ""),
        "drive": ("fwd, rwd, awd", "Привод: fwd, rwd, awd", ""),
        "engine_volume_l": ("Engine volume, liters", "Объём двигателя, л", "2.5"),
        "power_hp": ("Power, hp", "Мощность, л. с.", ""),
        "seats": ("Seats", "Мест", ""),
        "doors": ("Doors", "Дверей", ""),
        "odometer": (
            "Current odometer reading in the unit of the card",
            "Текущий пробег в единице карточки",
            "35000",
        ),
        "contexts": (
            "Allowed usage contexts, comma separated (rental); all applicable when empty",
            "Разрешённые контексты через запятую (rental); если пусто — все применимые",
            "rental",
        ),
        "owner_kind": (
            "Managed only: profile, person, organization or none",
            "Только для управления: profile, person, organization или none",
            "",
        ),
        "owner_lookup": (
            "Owner's c4rsys ID, email or phone (owner_kind = profile)",
            "c4rsys ID, email или телефон владельца (owner_kind = profile)",
            "",
        ),
        "owner_name": ("Owner's name (person or organization)", "Имя или название владельца", ""),
        "owner_contact": ("Owner's contact", "Контакт владельца", ""),
    }

    def references(self, company_id: UUID) -> list[tuple[str, list[list[str]]]]:
        rows = [["id", "name"]]
        rows += [[str(loc.id), loc.name] for loc in locations.locations_of(company_id)]
        return [("locations", rows)]

    def _new(self, scope: Scope, values: dict[str, str], out: Checked) -> dict[str, Any]:
        """The data of the card as the API of the garage takes them; problems go to `out`."""
        data: dict[str, Any] = {
            k: values.get(k, "")
            for k in (
                "vehicle_type",
                "plate",
                "plate_country",
                "vin",
                "color",
                "body_type",
                "fuel_type",
                "transmission",
                "drive",
                "engine_volume_l",
            )
        }
        data["make"], data["make_other"], data["vehicle_type"] = _make(
            values.get("make", ""), data["vehicle_type"]
        )
        data["model"], data["model_other"] = _model(values.get("model", ""), data["make"])
        for key in NUMBERS:
            text = values.get(key, "")
            if text:
                try:
                    data[key] = int(text)
                except ValueError:
                    out.error(key, "VALUE_INVALID")
        return data

    def check_row(self, scope: Scope, values: dict[str, str]) -> Checked:
        result = Checked()
        company_id = scope.company_id
        relation = values.get("relation", "").strip().lower()
        if not relation:
            result.error("relation", "FIELD_REQUIRED")
        elif relation not in (VehicleCard.Relation.OWN, VehicleCard.Relation.MANAGED):
            result.error("relation", "VALUE_UNKNOWN")
        unit = values.get("odometer_unit", "").strip().lower()
        if not unit:
            result.error("odometer_unit", "FIELD_REQUIRED")
        elif unit not in VehicleCard.OdometerUnit.values:
            result.error("odometer_unit", "VALUE_UNKNOWN")
        location_id: UUID | None = None
        text = values.get("location_id", "")
        if not text:
            result.error("location_id", "FIELD_REQUIRED")
        else:
            try:
                location_id = UUID(text)
                locations.require_usable(company_id, location_id, "location_id")
            except ValueError:
                result.error("location_id", "LOCATION_UNKNOWN")
                location_id = None
            except errors.ApiError as exc:
                result.error("location_id", exc.code)
                location_id = None
        # Import does not open a location the employee has no access to (§ 22.5.1).
        if location_id is not None and not access.RightsResolver().has_permission(
            scope.actor, "vehicle.create", location_id=location_id
        ):
            result.error("location_id", "LOCATION_NOT_ALLOWED")
        data = self._new(scope, values, result)
        details = None
        try:
            details = characteristics.parse(data, timezone.localdate().year)
        except errors.ApiError as exc:
            result.error(exc.field or "", exc.code)
        if details is not None and not _name(values, details):
            result.error("name", "NAME_NOT_DETERMINED")
        odometer = values.get("odometer", "")
        if odometer:
            try:
                if Decimal(odometer.replace(",", ".")) < 0:
                    result.error("odometer", "ODOMETER_INVALID")
            except InvalidOperation:
                result.error("odometer", "ODOMETER_INVALID")
        contexts = [c.strip().lower() for c in values.get("contexts", "").split(",") if c.strip()]
        if contexts and not set(contexts) <= services.applicable_contexts(company_id):
            result.error("contexts", "CONTEXT_NOT_APPLICABLE")
        owner_given = any(values.get(k) for k in OWNER_COLUMNS)
        if relation == VehicleCard.Relation.OWN and owner_given:
            result.error("owner_kind", "FIELD_NOT_APPLICABLE")
        elif relation == VehicleCard.Relation.MANAGED:
            try:
                owners.parse({k: values.get(k, "") for k in OWNER_COLUMNS}, relation)
            except errors.ApiError as exc:
                result.error(exc.field or "owner_kind", exc.code)
        vin = (details.vin if details else values.get("vin", "")).upper()
        plate = characteristics.normalize_plate(values.get("plate", ""))
        if vin and scope.repeated("vin", vin):
            result.error("vin", "VIN_REPEATED_IN_FILE")
        if plate and scope.repeated("plate", f"{values.get('plate_country', '').upper()}:{plate}"):
            result.error("plate", "PLATE_REPEATED_IN_FILE")
        self._matches(company_id, values, vin, plate, result)
        return result

    def _matches(
        self, company_id: UUID, values: dict[str, str], vin: str, plate: str, out: Checked
    ) -> None:
        """By the VHL ID, then by the VIN and the plate — only among the company's cards
        (§ 22.2.3, § 8.6). A name is not enough."""
        cards = services.cards_of(company_id)
        by_id: set[str] = set()
        vehicle_id = values.get("vehicle_id", "").strip().upper()
        if vehicle_id:
            found = cards.filter(vehicle__public_id=vehicle_id).first()
            if found is None:
                out.error("vehicle_id", "VEHICLE_ID_UNKNOWN")
            else:
                out.match(found.id, found.name, "vehicle_id")
                by_id.add(str(found.id))
        others: set[str] = set()
        if vin:
            for card in cards.filter(vehicle__vin=vin)[:5]:
                out.match(card.id, card.name, "vin")
                others.add(str(card.id))
        if plate:
            current = VehiclePlate.objects.filter(number_normalized=plate, valid_to__isnull=True)
            country = values.get("plate_country", "").upper()
            if country:
                current = current.filter(country=country)
            for card in cards.filter(vehicle__in=current.values("vehicle_id"))[:5]:
                out.match(card.id, card.name, "plate")
                others.add(str(card.id))
        out.conflict = bool(by_id and others - by_id)

    def create(
        self,
        ctx: CommandContext,
        company: Company,
        values: dict[str, str],
        extra: dict[str, Any],
        *,
        duplicate: bool,
        operation_id: UUID,
    ) -> UUID:
        relation = values["relation"].strip().lower()
        scope = Scope(company.id, ctx.actor)
        unused = Checked()
        details = characteristics.parse(self._new(scope, values, unused), timezone.localdate().year)
        contexts = [c.strip().lower() for c in values.get("contexts", "").split(",") if c.strip()]
        card = services.create_vehicle(
            ctx,
            company,
            services.NewVehicle(
                name=_name(values, details),
                location_id=UUID(values["location_id"]),
                odometer_unit=values["odometer_unit"].strip().lower(),
                details=details,
                relation=relation,
                owner=owners.parse({k: values.get(k, "") for k in OWNER_COLUMNS}, relation),
                contexts=tuple(contexts) if contexts else None,
                possible_duplicate=duplicate,
            ),
        )
        if values.get("odometer"):
            services.record_reading(
                ctx,
                card,
                measured_at=timezone.now(),
                value=Decimal(values["odometer"].replace(",", ".")),
                source_type="import",
                source_id=operation_id,
            )
        return card.id

    def remove(self, ctx: CommandContext, company_id: UUID, record_ids: list[UUID]) -> None:
        services.remove_imported(company_id, record_ids)


ADAPTER = VehiclesTemplate()
