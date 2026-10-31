"""The vehicle as its confirmed owner sees it in the personal garage (ТЗ § 7.1.2, § 8.2.3,
§ 8.2.7, § 8.3.6–8.3.9, § 8.3.15, § 9.1.4, § 9.1.16, § 10.6.1).

* The base data of the minimal garage (§ 7.1.2) and the period of ownership.
* For each company that asked to manage the vehicle: the offered categories before the
  confirmation, their content after it (`disclosure`).
* The global documents with versions and the copies of the companies to decide on.
* After a sale by a company (§ 9.1.4, § 9.1.16): the history of the works done before the sale —
  the date, the mileage, what was repaired or replaced — and the maintenance rules of the vehicle,
  to read. Internal documents, settlements with the service and expenses are not passed, nor the
  inspections of the company (§ 10.6.1).
* The seller's requests to confirm a wrong transfer (§ 8.3.9).
"""

from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID

from c4rsys.fleet import characteristics, disclosure, identity, lifecycle, vehicle_documents
from c4rsys.fleet.models import (
    Vehicle,
    VehicleCard,
    VehicleManagementConfirmation,
    VehicleOwnerRequest,
    VehicleOwnership,
)
from c4rsys.platform import errors

# (company_id, card_id, until) -> works done before the moment: {"at", "work", "odometer", ...}
WorkHistory = Callable[[UUID, UUID, datetime | None], list[dict[str, Any]]]
# (company_id, card_id) -> the maintenance rules: {"name", "system_work", intervals ...}
Rules = Callable[[UUID, UUID], list[dict[str, Any]]]
_works: list[WorkHistory] = []
_rules: list[Rules] = []


def register_work_history(provider: WorkHistory) -> None:
    _works.append(provider)


def register_rules(provider: Rules) -> None:
    _rules.append(provider)


def _ownership(user_id: UUID, public_id: str) -> VehicleOwnership:
    found = (
        VehicleOwnership.objects.select_related("vehicle")
        .filter(owner_user_id=user_id, ended_at__isnull=True, vehicle__public_id=public_id)
        .first()
    )
    if found is None:
        raise errors.not_found()
    return found


def vehicle_of(user_id: UUID, public_id: str) -> Vehicle:
    return _ownership(user_id, public_id).vehicle


def _iso(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v.isoformat() if isinstance(v, datetime) else v for k, v in row.items()}


def _managers(vehicle: Vehicle, user_id: UUID) -> list[VehicleCard]:
    entities = identity.family(identity.resolve(vehicle.id))
    ids = set(
        VehicleOwnerRequest.objects.filter(
            vehicle_id__in=entities, target_user_id=user_id, target_company_id__isnull=True
        ).values_list("vehicle_card_id", flat=True)
    ) | set(
        VehicleManagementConfirmation.objects.filter(
            vehicle_id__in=entities, owner_user_id=user_id
        ).values_list("vehicle_card_id", flat=True)
    )
    return list(VehicleCard.objects.select_related("company").filter(pk__in=ids))


def _seller_history(ownership: VehicleOwnership) -> dict[str, Any] | None:
    """§ 9.1.4, § 9.1.16: what the buyer gets from the selling company."""
    if ownership.basis != "sale" or ownership.seller_company_id is None:
        return None
    entities = identity.family(identity.resolve(ownership.vehicle_id))
    cards = VehicleCard.objects.select_related("company").filter(
        company_id=ownership.seller_company_id, vehicle_id__in=entities
    )
    works: list[dict[str, Any]] = []
    rules: list[dict[str, Any]] = []
    company = ""
    for card in cards:
        company = card.company.display_name
        for provider in _works:
            works += provider(card.company_id, card.id, ownership.started_at)
        for rules_provider in _rules:
            rules += rules_provider(card.company_id, card.id)
    works.sort(key=lambda w: w["at"].timestamp() if w.get("at") else 0, reverse=True)
    return {"company": company, "works": [_iso(w) for w in works], "rules": rules}


def detail(user_id: UUID, public_id: str) -> dict[str, Any]:
    ownership = _ownership(user_id, public_id)
    vehicle = ownership.vehicle
    facts = characteristics.describe(vehicle)
    make, model = facts["make_name"] or "", facts["model_name"] or ""
    return {
        "vehicle_id": vehicle.public_id,
        "name": " ".join(p for p in (make, model) if p) or vehicle.public_id,
        "make": make,
        "model": model,
        "plate": facts["plate"],
        "year": facts["year"],
        "vin": facts["vin"],
        "since": ownership.started_at.isoformat(),
        "basis": ownership.basis,
        "managers": [
            disclosure.for_owner(c, owner_user_id=user_id, owner_company_id=None)
            for c in _managers(vehicle, user_id)
        ],
        "documents": vehicle_documents.documents_payload(vehicle.id),
        "copies": vehicle_documents.copies_for_owner(vehicle.id, user_id, None),
        "seller_history": _seller_history(ownership),
        "reversals": [
            lifecycle.reversal_payload(r)
            for r in lifecycle.reversals_for_user(user_id)
            if r.vehicle_id == vehicle.id or r.ownership_id == ownership.id
        ],
    }


def open_reversals(user_id: UUID) -> list[dict[str, Any]]:
    return [
        lifecycle.reversal_payload(r)
        for r in lifecycle.reversals_for_user(user_id)
        if r.state == "open"
    ]
