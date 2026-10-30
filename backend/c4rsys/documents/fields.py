"""Fields of templates and their values (ТЗ § 18.2; customer's decision Р-3 of 29.10.2026).

A field is `{{source.field}}`, a repeated table `{{table:source.collection}}` (§ 18.2.2). The
first part names the source or role of the data. Values are taken from the working cards of the
company — for a client the working values of its card (§ 18.2.5) — and are fixed in the document
when it is made (§ 18.2.4). The catalog is the agreed system fields; no custom fields (§ 18.2.6).
Each module is read through its own service functions.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from django.utils import timezone

from c4rsys.companies.models import Company

# code -> the source it needs ("" — always available).
FIELDS: dict[str, str] = {
    "company.name": "",
    "company.legal_name": "",
    "company.address": "",
    "company.phone": "",
    "company.email": "",
    "document.number": "",
    "document.date": "",
    "employee.full_name": "",
    "location.name": "location",
    "location.address": "location",
    "client.full_name": "client",
    "client.phone": "client",
    "client.email": "client",
    "client.birth_date": "client",
    "vehicle.name": "vehicle",
    "vehicle.make": "vehicle",
    "vehicle.model": "vehicle",
    "vehicle.year": "vehicle",
    "vehicle.vin": "vehicle",
    "vehicle.plate": "vehicle",
    "vehicle.color": "vehicle",
    "rental.booking_number": "rental",
    "rental.start": "rental",
    "rental.planned_end": "rental",
    "rental.end": "rental",
    "rental.tariff": "rental",
    "rental.cost": "rental",
    "rental.deposit": "rental",
    "rental.issue_place": "rental",
    "rental.return_place": "rental",
    "inspection.issue_odometer": "rental",
    "inspection.issue_fuel": "rental",
    "inspection.return_odometer": "rental",
    "inspection.return_fuel": "rental",
    "order.number": "order",
    "order.charged": "order",
    "order.paid": "order",
    "order.balance": "order",
    "service.number": "service",
    "service.reason": "service",
    "service.total": "service",
    "delivery.number": "delivery",
    "delivery.from": "delivery",
    "delivery.to": "delivery",
    "delivery.planned_start": "delivery",
    "delivery.price": "delivery",
}

# table code -> its columns.
TABLES: dict[str, tuple[str, ...]] = {
    "order.lines": ("name", "amount"),
    "rental.charges": ("name", "amount"),
    "service.works": ("name", "executor", "price"),
    "service.materials": ("name", "quantity", "amount"),
}

LABELS = {
    "rental_base": {"en": "Rental", "ru": "Аренда", "th": "ค่าเช่า"},
    "rental_mileage": {"en": "Mileage over the limit", "ru": "Перепробег", "th": "ระยะเกิน"},
    "rental_manual": {"en": "Additional charge", "ru": "Доначисление", "th": "ค่าใช้จ่ายเพิ่มเติม"},
    "rental_service": {
        "en": "Additional service",
        "ru": "Дополнительная услуга",
        "th": "บริการเพิ่มเติม",
    },
    "sale_line": {"en": "Goods", "ru": "Товар", "th": "สินค้า"},
    "service_job": {"en": "Service work", "ru": "Сервисная работа", "th": "งานบริการ"},
    "service_material": {"en": "Part", "ru": "Запчасть", "th": "อะไหล่"},
    "delivery": {"en": "Delivery", "ru": "Доставка", "th": "การส่งรถ"},
    "damage": {"en": "Damage", "ru": "Повреждение", "th": "ความเสียหาย"},
}
DISCOUNT = {"en": "Discount", "ru": "Скидка", "th": "ส่วนลด"}


def _label(source_type: str, language: str) -> str:
    """The discount of a position is named «Discount: <position>»."""
    if source_type.startswith("discount_"):
        position = _label(source_type.removeprefix("discount_"), language)
        return f"{DISCOUNT.get(language, DISCOUNT['en'])}: {position}"
    return LABELS.get(source_type, {}).get(language) or source_type


@dataclass
class Values:
    fields: dict[str, str] = field(default_factory=dict)
    tables: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    links: list[tuple[str, UUID]] = field(default_factory=list)
    location_id: UUID | None = None


def _money(amount: Decimal | None, currency: str | None) -> str:
    if amount is None:
        return ""
    return f"{Decimal(amount):,.2f} {currency or ''}".replace(",", " ").strip()


def _day(value: date | None) -> str:
    return value.strftime("%d.%m.%Y") if value else ""


def _moment(value: datetime | None, zone: str) -> str:
    if value is None:
        return ""
    return timezone.localtime(value, ZoneInfo(zone or "UTC")).strftime("%d.%m.%Y %H:%M")


def _set(values: Values, code: str, value: Any) -> None:
    text = "" if value is None else str(value)
    if text:
        values.fields[code] = text


def _company(values: Values, company: Company, number: int | None, on: date, author: str) -> None:
    _set(values, "company.name", company.legal_name or company.display_name)
    _set(values, "company.legal_name", company.legal_name)
    _set(values, "company.address", company.legal_address or company.actual_address)
    _set(values, "company.phone", company.phone)
    _set(values, "company.email", company.email)
    _set(values, "document.number", number)
    _set(values, "document.date", _day(on))
    _set(values, "employee.full_name", author)


def _location(values: Values, company_id: UUID, location_id: UUID | None) -> str:
    from c4rsys.companies import locations

    if location_id is None:
        return ""
    values.location_id = values.location_id or location_id
    found = locations.locations_of(company_id).filter(pk=location_id).first()
    if found is None:
        return ""
    _set(values, "location.name", found.name)
    _set(values, "location.address", found.address)
    return found.timezone


def _client(values: Values, company_id: UUID, client_id: UUID | None) -> None:
    from c4rsys.parties import services as parties

    if client_id is None:
        return
    card = parties.get_client(company_id, client_id)
    values.links.append(("client", card.id))
    name = card.company_name or " ".join(
        x for x in (card.last_name, card.first_name, card.middle_name) if x
    )
    _set(values, "client.full_name", name)
    _set(values, "client.birth_date", _day(card.birth_date))
    for contact in card.contacts.all():
        _set(
            values,
            f"client.{contact.kind}",
            values.fields.get(f"client.{contact.kind}") or contact.value,
        )


def _vehicle(values: Values, company_id: UUID, card_id: UUID | None) -> None:
    from c4rsys.fleet import characteristics
    from c4rsys.fleet import services as fleet

    if card_id is None:
        return
    card = fleet.get_card(company_id, card_id)
    values.links.append(("vehicle", card.id))
    described = characteristics.describe(card.vehicle)
    _set(values, "vehicle.name", card.name)
    _set(values, "vehicle.make", described.get("make_name"))
    _set(values, "vehicle.model", described.get("model_name"))
    _set(values, "vehicle.year", described.get("year"))
    _set(values, "vehicle.vin", described.get("vin"))
    _set(values, "vehicle.plate", described.get("plate"))
    _set(values, "vehicle.color", described.get("color"))
    if values.location_id is None:
        values.location_id = card.accounting_location_id


def _order(values: Values, company_id: UUID, order_id: UUID, language: str) -> None:
    from c4rsys.finance.models import ChargeEntry
    from c4rsys.orders import services as orders

    order = orders.get_order(company_id, order_id)
    values.links.append(("order", order.id))
    _set(values, "order.number", order.number)
    amounts = orders.amounts_of(order)
    if amounts:
        currency, money = next(iter(sorted(amounts.items())))
        _set(values, "order.charged", _money(money.charged, currency))
        _set(values, "order.paid", _money(money.paid, currency))
        _set(values, "order.balance", _money(money.balance, currency))
    rows: dict[tuple[str, UUID], Decimal] = {}
    currencies: dict[tuple[str, UUID], str] = {}
    for entry in ChargeEntry.objects.filter(company_id=company_id, order_id=order.id):
        k = (entry.source_type, entry.source_id)
        rows[k] = rows.get(k, Decimal(0)) + entry.amount
        currencies[k] = entry.currency_id
    lines = []
    for (source_type, source_id), amount in rows.items():
        if amount == 0:
            continue
        label = _label(source_type, language)
        lines.append(
            {"name": label, "amount": _money(amount, currencies[(source_type, source_id)])}
        )
    values.tables["order.lines"] = lines
    if order.client_id is not None:
        _client(values, company_id, order.client_id)


def _rental(values: Values, company_id: UUID, rental_id: UUID, language: str) -> None:
    from c4rsys.finance.models import ChargeEntry
    from c4rsys.inspections import services as inspections
    from c4rsys.rentals import billing, rentals

    rental = rentals.get_rental(company_id, rental_id)
    values.links.append(("rental", rental.id))
    zone = _location(values, company_id, rental.issue_location_id) or rental.calc_timezone
    _set(values, "rental.booking_number", rental.booking_number)
    _set(values, "rental.start", _moment(rental.actual_start or rental.planned_start, zone))
    _set(values, "rental.planned_end", _moment(rental.planned_end, zone))
    _set(values, "rental.end", _moment(rental.actual_end, zone))
    snapshot = rental.tariff_snapshot or {}
    _set(values, "rental.tariff", snapshot.get("name"))
    found = billing.base_calculation(rental)
    if found is not None:
        _set(values, "rental.cost", _money(found[1].base_amount, found[1].currency))
    deposit = (snapshot.get("vehicle_terms") or {}).get("deposit") or {}
    if deposit.get("required"):
        _set(
            values,
            "rental.deposit",
            _money(Decimal(str(deposit.get("amount"))), snapshot.get("currency")),
        )
    issue_place = rental.issue_place_note or rental.issue_place_address
    _set(values, "rental.issue_place", issue_place or values.fields.get("location.name"))
    _set(values, "rental.return_place", rental.return_place_note)
    for kind in ("issue", "return"):
        inspection = inspections.readings_of_rental(rental.id, kind)
        if inspection is None:
            continue
        prefix = f"inspection.{kind}"
        if inspection.odometer_value is not None:
            _set(
                values,
                f"{prefix}_odometer",
                f"{inspection.odometer_value} {inspection.odometer_unit}",
            )
        _set(values, f"{prefix}_fuel", inspection.fuel_value)
    charges: dict[str, Decimal] = {}
    currency = ""
    for entry in ChargeEntry.objects.filter(
        company_id=company_id,
        order_id=rental.order_id,
        source_type__in=(
            "rental_base",
            "rental_mileage",
            "rental_manual",
            "discount_rental_base",
            "discount_rental_mileage",
            "discount_rental_manual",
        ),
    ):
        # Positions are net of their discounts, like the order's totals.
        kind = entry.source_type.removeprefix("discount_")
        charges[kind] = charges.get(kind, Decimal(0)) + entry.amount
        currency = entry.currency_id
    values.tables["rental.charges"] = [
        {"name": LABELS[k].get(language, k), "amount": _money(v, currency)}
        for k, v in charges.items()
        if v != 0
    ]
    _vehicle(values, company_id, rental.vehicle_card_id)
    _order(values, company_id, rental.order_id, language)
    if rental.client_id is not None and ("client", rental.client_id) not in values.links:
        _client(values, company_id, rental.client_id)


def _service(values: Values, company_id: UUID, operation_id: UUID) -> None:
    from c4rsys.companies import staff
    from c4rsys.parties import counterparties
    from c4rsys.service import materials, operations

    op = operations.get_operation(company_id, operation_id)
    values.links.append(("service_operation", op.id))
    _location(values, company_id, op.location_id)
    _set(values, "service.number", op.number)
    _set(values, "service.reason", op.reason)
    jobs = operations.jobs_of(op)
    names = staff.employee_names(company_id, {j.executor_id for j in jobs if j.executor_id})
    parties = counterparties.names(company_id, {j.contractor_id for j in jobs if j.contractor_id})
    done = [j for j in jobs if j.state in ("done", "terminated")]
    total = sum((j.price or Decimal(0) for j in done), Decimal(0))
    rows = []
    for j in done:
        executor = (
            names.get(j.executor_id)
            if j.executor_id
            else parties.get(j.contractor_id)
            if j.contractor_id
            else ""
        )
        rows.append(
            {
                "name": str(j.snapshot.get("name", "")),
                "executor": executor or "",
                "price": _money(j.price, j.currency_id),
            }
        )
    values.tables["service.works"] = rows
    parts = []
    for state in materials.states_of(materials.of_jobs([j.id for j in jobs])):
        if state.consumed <= 0:
            continue
        amount = materials.amount_of(state)
        total += amount or Decimal(0)
        parts.append(
            {
                "name": state.material.item_name,
                "quantity": f"{state.consumed.normalize():f}",
                "amount": _money(amount, state.material.currency_id),
            }
        )
    values.tables["service.materials"] = parts
    currency = jobs[0].currency_id if jobs else ""
    _set(values, "service.total", _money(total, currency))
    _vehicle(values, company_id, op.vehicle_card_id)
    _order(values, company_id, op.order_id, "")


def _delivery(values: Values, company_id: UUID, delivery_id: UUID, language: str) -> None:
    from c4rsys.companies import locations
    from c4rsys.delivery import services as deliveries

    d = deliveries.get(company_id, delivery_id)
    values.links.append(("delivery", d.id))
    zone = _location(values, company_id, d.from_location_id)
    names = dict(locations.locations_of(company_id).values_list("id", "name"))

    def point(side: str) -> str:
        place = names.get(getattr(d, f"{side}_location_id"), "")
        if getattr(d, f"{side}_kind") == "location":
            return str(place)
        detail = getattr(d, f"{side}_address") or getattr(d, f"{side}_name")
        return f"{place} / {detail}"

    _set(values, "delivery.number", d.number)
    _set(values, "delivery.from", point("from"))
    _set(values, "delivery.to", point("to"))
    _set(values, "delivery.planned_start", _moment(d.planned_start, zone))
    _set(values, "delivery.price", _money(d.price, d.currency_id))
    _vehicle(values, company_id, d.vehicle_card_id)
    _order(values, company_id, d.order_id, language)


def collect(
    company: Company,
    source_type: str,
    source_id: UUID | None,
    *,
    number: int | None,
    on: date,
    author: str,
    language: str,
) -> Values:
    """Values of every field the source gives; the links of the document follow them."""
    values = Values()
    _company(values, company, number, on, author)
    if source_id is None:
        return values
    if source_type == "rental":
        _rental(values, company.id, source_id, language)
    elif source_type == "order":
        _order(values, company.id, source_id, language)
    elif source_type == "service_operation":
        _service(values, company.id, source_id)
    elif source_type == "delivery":
        _delivery(values, company.id, source_id, language)
    elif source_type == "client":
        _client(values, company.id, source_id)
    elif source_type == "vehicle":
        _vehicle(values, company.id, source_id)
    seen: set[tuple[str, UUID]] = set()
    values.links = [x for x in values.links if not (x in seen or seen.add(x))]  # type: ignore[func-returns-value]
    return values
