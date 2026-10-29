"""Objects a task may be linked to (§ 19.1.3, § 19.1.7). The main object bounds the scope: a task
of an operation or a vehicle belongs to its location."""

from collections.abc import Callable
from typing import Any
from uuid import UUID

from c4rsys.platform import errors

TYPES = (
    "client",
    "counterparty",
    "vehicle",
    "order",
    "rental",
    "service_operation",
    "delivery",
    "document",
)


def _document(company_id: UUID, document_id: UUID) -> Any:
    from c4rsys.documents import services as documents

    found = documents.documents_of(company_id).filter(pk=document_id).first()
    if found is None:
        raise errors.not_found()
    return found


def _getters() -> dict[str, Callable[[UUID, UUID], Any]]:
    from c4rsys.delivery import services as deliveries
    from c4rsys.fleet import services as fleet
    from c4rsys.orders import services as orders
    from c4rsys.parties import counterparties
    from c4rsys.parties import services as parties
    from c4rsys.rentals import rentals
    from c4rsys.service import operations

    return {
        "client": parties.get_client,
        "counterparty": counterparties.get,
        "vehicle": fleet.get_card,
        "order": orders.get_order,
        "rental": rentals.get_rental,
        "service_operation": operations.get_operation,
        "delivery": deliveries.get,
        "document": _document,
    }


def resolve(company_id: UUID, object_type: str, object_id: UUID, field: str = "object") -> Any:
    getter = _getters().get(object_type)
    if getter is None:
        raise errors.validation_error(field, code="TASK_OBJECT_TYPE_UNKNOWN")
    try:
        return getter(company_id, object_id)
    except errors.ApiError as exc:
        raise errors.validation_error(field, code="TASK_OBJECT_UNKNOWN") from exc


PLACES = {
    "rental": "issue_location_id",
    "service_operation": "location_id",
    "delivery": "from_location_id",
    "vehicle": "accounting_location_id",
}


def place_of(object_type: str, obj: Any) -> UUID | None:
    """The location of the object, when it has one; a document of one location too."""
    if object_type == "document":
        from c4rsys.documents import services as documents

        if obj.all_locations:
            return None
        found = sorted(documents.location_ids(obj))
        return found[0] if len(found) == 1 else None
    attribute = PLACES.get(object_type)
    return getattr(obj, attribute, None) if attribute else None
