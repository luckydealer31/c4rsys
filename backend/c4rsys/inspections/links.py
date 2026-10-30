"""What inspections need to know of rentals and service operations. Those modules register
readers and handlers here, so this module does not read their tables (A04)."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext

if TYPE_CHECKING:
    from c4rsys.inspections.models import Inspection


@dataclass(frozen=True)
class RentalLink:
    id: UUID
    vehicle_card_id: UUID
    state: str
    order_id: UUID
    client_id: UUID | None
    issue_location_id: UUID | None
    return_location_id: UUID | None
    issued: bool  # active or returned
    closed: bool  # the formalization is completed (§ 14.5.6)


@dataclass(frozen=True)
class ServiceLink:
    id: UUID
    vehicle_card_id: UUID
    state: str
    location_id: UUID | None


RentalReader = Callable[[UUID, UUID], RentalLink | None]
ServiceReader = Callable[[UUID, UUID], ServiceLink | None]
# Called in the command that saves an inspection (rentals: the mileage overage, decision Т5).
SavedHandler = Callable[[CommandContext, "Inspection"], None]

_rental: list[RentalReader] = []
_service: list[ServiceReader] = []
_on_saved: list[SavedHandler] = []


def register_rental_reader(reader: RentalReader) -> None:
    _rental.append(reader)


def register_service_reader(reader: ServiceReader) -> None:
    _service.append(reader)


def register_on_saved(handler: SavedHandler) -> None:
    _on_saved.append(handler)


def rental(company_id: UUID, rental_id: UUID) -> RentalLink | None:
    return next((r for p in _rental if (r := p(company_id, rental_id)) is not None), None)


def service_operation(company_id: UUID, operation_id: UUID) -> ServiceLink | None:
    return next((s for p in _service if (s := p(company_id, operation_id)) is not None), None)


def require_rental(company_id: UUID, rental_id: UUID) -> RentalLink:
    found = rental(company_id, rental_id)
    if found is None:
        raise errors.validation_error("rental_id", code="RENTAL_UNKNOWN")
    return found


def saved(ctx: CommandContext, inspection: "Inspection") -> None:
    for handler in _on_saved:
        handler(ctx, inspection)
