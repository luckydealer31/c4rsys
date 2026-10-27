"""Catalog of additional rental services (ТЗ § 13.4.5–13.4.8, § 23.4.4; customer's decisions
Т7, У2, У3, У12 of 27.10.2026).

A service is paid once, by quantity (a price per piece) or by duration (a rate per unit: minute,
hour, 24-hour day from the start of the period, calendar month; no ranges). It has its own
currency. It applies to the whole company or selected locations and may be limited to vehicles
and tariffs: within one kind «any of the selected», between kinds «and»; a standalone service is
checked by its location only. The catalog has the tariff rights, without rights of its own
(§ 23.4.4). Pricing, unit and currency do not change after creation; added services keep their
snapshot (§ 13.4.8).
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db.models import Q, QuerySet
from django.utils import timezone

from c4rsys.companies.models import Company
from c4rsys.fleet.models import VehicleCard
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.platform.models import Currency
from c4rsys.rentals import tariffs
from c4rsys.rentals.models import (
    ExtraService,
    ExtraServiceLocation,
    ExtraServiceTariff,
    ExtraServiceVehicle,
    Rental,
    Tariff,
)


def _ids(model: Any, service: ExtraService, field: str) -> list[str]:
    return sorted(
        str(x) for x in model.objects.filter(service=service).values_list(field, flat=True)
    )


def price_text(value: Decimal) -> str:
    """The same text before and after saving: «300», not «300.0000» read back."""
    return f"{Decimal(value).normalize():f}"


def snapshot(service: ExtraService) -> dict[str, Any]:
    return {
        "name": service.name,
        "pricing": service.pricing,
        "unit": service.unit or None,
        "price": price_text(service.price),
        "currency": service.currency_id,
        "all_locations": service.all_locations,
        "locations": _ids(ExtraServiceLocation, service, "location_id"),
        "vehicles": _ids(ExtraServiceVehicle, service, "vehicle_card_id"),
        "tariffs": _ids(ExtraServiceTariff, service, "tariff_id"),
        "state": service.state,
    }


def line_snapshot(service: ExtraService) -> dict[str, Any]:
    """The price and terms kept by an added service (§ 13.4.8)."""
    return {
        "service_id": str(service.id),
        "service_version": service.version,
        "name": service.name,
        "pricing": service.pricing,
        "unit": service.unit or None,
        "price": price_text(service.price),
        "currency": service.currency_id,
        "currency_minor_units": service.currency.minor_units,
    }


def availability_of(service: ExtraService) -> tariffs.Availability:
    return tariffs.Availability(
        all_locations=service.all_locations,
        location_ids=frozenset(
            ExtraServiceLocation.objects.filter(service=service).values_list(
                "location_id", flat=True
            )
        ),
    )


@dataclass(frozen=True)
class Scope:
    """Where the service applies; None in `location_ids` — the whole company."""

    location_ids: frozenset[UUID] | None
    vehicle_ids: frozenset[UUID]
    tariff_ids: frozenset[UUID]


def _validate_scope(company_id: UUID, scope: Scope) -> None:
    if scope.location_ids:
        tariffs.validate_locations(company_id, scope.location_ids)
    found = VehicleCard.objects.filter(company_id=company_id, pk__in=scope.vehicle_ids).count()
    if found != len(scope.vehicle_ids):
        raise errors.validation_error("vehicles", code="VEHICLE_UNKNOWN")
    found = Tariff.objects.filter(company_id=company_id, pk__in=scope.tariff_ids).count()
    if found != len(scope.tariff_ids):
        raise errors.validation_error("tariffs", code="TARIFF_UNKNOWN")


def _write_scope(service: ExtraService, scope: Scope) -> None:
    company_id = service.company_id
    ExtraServiceLocation.objects.filter(service=service).delete()
    ExtraServiceVehicle.objects.filter(service=service).delete()
    ExtraServiceTariff.objects.filter(service=service).delete()
    ExtraServiceLocation.objects.bulk_create(
        ExtraServiceLocation(company_id=company_id, service=service, location_id=x)
        for x in sorted(scope.location_ids or ())
    )
    ExtraServiceVehicle.objects.bulk_create(
        ExtraServiceVehicle(company_id=company_id, service=service, vehicle_card_id=x)
        for x in sorted(scope.vehicle_ids)
    )
    ExtraServiceTariff.objects.bulk_create(
        ExtraServiceTariff(company_id=company_id, service=service, tariff_id=x)
        for x in sorted(scope.tariff_ids)
    )


def parse_pricing(data: dict[str, Any]) -> tuple[str, str]:
    pricing = str(data.get("pricing") or "")
    if pricing not in ExtraService.Pricing.values:
        raise errors.validation_error("pricing", code="CHOICE_REQUIRED")
    if pricing != ExtraService.Pricing.TIME:
        return pricing, ""
    unit = str(data.get("unit") or "")
    if unit not in ExtraService.Unit.values:
        raise errors.validation_error("unit", code="CHOICE_REQUIRED")
    return pricing, unit


def parse_price(value: Any) -> Decimal:
    return tariffs.parse_amount(value, "price")


@dataclass(frozen=True)
class NewService:
    name: str
    pricing: str
    unit: str
    price: Decimal
    currency: str  # empty: the company's main currency
    scope: Scope


def create(ctx: CommandContext, company: Company, data: NewService) -> ExtraService:
    code = data.currency or (company.main_currency_id or "")
    currency = Currency.objects.filter(code=code.upper()).first() if code else None
    if currency is None:
        raise errors.validation_error("currency", code="CURRENCY_INVALID")
    _validate_scope(company.id, data.scope)
    service = ExtraService.objects.create(
        company=company,
        name=data.name,
        pricing=data.pricing,
        unit=data.unit,
        price=data.price,
        currency=currency,
        all_locations=data.scope.location_ids is None,
        created_by_user_id=ctx.actor.user_id,
        created_at=timezone.now(),
    )
    _write_scope(service, data.scope)
    ctx.audit(
        "extra_service.created",
        object_type="extra_service",
        object_id=service.id,
        after=snapshot(service),
    )
    return service


def locked(company_id: UUID, service_id: UUID, expected_version: int) -> ExtraService:
    service = (
        ExtraService.objects.select_for_update()
        .filter(company_id=company_id, pk=service_id)
        .first()
    )
    if service is None:
        raise errors.not_found()
    if service.version != expected_version:
        raise errors.version_conflict()
    return service


@dataclass(frozen=True)
class Changes:
    name: str | None = None
    price: Decimal | None = None
    scope: Scope | None = None


def change(ctx: CommandContext, service: ExtraService, changes: Changes) -> ExtraService:
    """Name, price and applicability; added services keep their snapshot (§ 13.4.8)."""
    before = snapshot(service)
    if changes.name is not None:
        service.name = changes.name
    if changes.price is not None:
        service.price = changes.price
    if changes.scope is not None:
        _validate_scope(service.company_id, changes.scope)
        service.all_locations = changes.scope.location_ids is None
    service.version += 1
    service.save()
    if changes.scope is not None:
        _write_scope(service, changes.scope)
    after = snapshot(service)
    ctx.audit(
        "extra_service.changed",
        object_type="extra_service",
        object_id=service.id,
        before={k: v for k, v in before.items() if v != after[k]},
        after={k: v for k, v in after.items() if v != before[k]},
    )
    return service


def set_archived(ctx: CommandContext, service: ExtraService, *, archived: bool) -> ExtraService:
    """An archived service is not offered for new additions; it can be restored."""
    target = ExtraService.State.ARCHIVED if archived else ExtraService.State.ACTIVE
    if service.state == target:
        raise errors.business_rule("SERVICE_STATE_UNCHANGED", state=service.state)
    before = service.state
    service.state = target
    service.version += 1
    service.save(update_fields=["state", "version", "updated_at"])
    ctx.audit(
        "extra_service.archived" if archived else "extra_service.restored",
        object_type="extra_service",
        object_id=service.id,
        before={"state": before},
        after={"state": target},
    )
    return service


# --- Applicability (§ 13.4.7; decision У3) ------------------------------------------------------


def at_location(company_id: UUID, location_id: UUID) -> QuerySet[ExtraService]:
    """Active services available in the location: for the whole company or listing it."""
    return (
        ExtraService.objects.filter(company_id=company_id, state=ExtraService.State.ACTIVE)
        .filter(
            Q(all_locations=True)
            | Q(
                pk__in=ExtraServiceLocation.objects.filter(location_id=location_id).values(
                    "service"
                )
            )
        )
        .select_related("currency")
    )


def rental_location(rental: Rental) -> UUID | None:
    """The location of a rental for its services: the issue location; a booking without one —
    the vehicle's location."""
    if rental.issue_location_id is not None:
        return rental.issue_location_id
    return (
        VehicleCard.objects.filter(pk=rental.vehicle_card_id)
        .values_list("accounting_location_id", flat=True)
        .first()
    )


def for_rental(rental: Rental) -> QuerySet[ExtraService]:
    """Services that may be linked to the rental: its location and its vehicle and tariff when
    the service is limited to them. A booking without a tariff fits only services without a tariff
    limit."""
    location_id = rental_location(rental)
    if location_id is None:
        return ExtraService.objects.none()
    limited_vehicles = ExtraServiceVehicle.objects.values("service")
    limited_tariffs = ExtraServiceTariff.objects.values("service")
    of_tariff = (
        ExtraServiceTariff.objects.filter(tariff_id=rental.tariff_id)
        if rental.tariff_id is not None
        else ExtraServiceTariff.objects.none()
    )
    return (
        at_location(rental.company_id, location_id)
        .filter(
            ~Q(pk__in=limited_vehicles)
            | Q(
                pk__in=ExtraServiceVehicle.objects.filter(
                    vehicle_card_id=rental.vehicle_card_id
                ).values("service")
            )
        )
        .filter(~Q(pk__in=limited_tariffs) | Q(pk__in=of_tariff.values("service")))
    )
