"""Final readiness check of the initial setup (ТЗ § 22.1.12).

Readiness is assessed for the company, then separately for each location and each of its
contexts; an unfinished context does not block another. A rental context is ready for the first
operation with a current vehicle rental tariff and a suitable vehicle: own or managed, bound to
the location, allowed for rental, with that tariff assigned (§ 22.1.8–22.1.9). Standalone
additional services, service and warehouse modules come later.
"""

from typing import Any
from uuid import UUID

from c4rsys.companies import locations
from c4rsys.companies.models import ActivityContext, Company
from c4rsys.fleet import services as fleet
from c4rsys.rentals import tariffs


def _rental_missing(company_id: UUID, location_id: UUID, enabled: bool) -> list[str]:
    if not enabled:
        return ["context_disabled"]
    missing = []
    if not tariffs.available_at(company_id, location_id).exists():
        missing.append("no_tariff")
    cards = [
        card
        for card in fleet.cards_of(company_id).filter(accounting_location_id=location_id)
        if fleet.rental_readiness(card)["ready"]
    ]
    if not cards:
        missing.append("no_vehicle")
    elif not any(tariffs.tariffs_for_vehicle(card).exists() for card in cards):
        missing.append("no_vehicle_with_tariff")
    return missing


def readiness(company: Company) -> dict[str, Any]:
    base = {
        "display_name": bool(company.display_name),
        "timezone": bool(company.timezone),
        "main_currency": company.main_currency_id is not None,
    }
    result_locations: list[dict[str, Any]] = []
    for location in locations.locations_of(company.id):
        contexts = []
        for context in sorted(location.contexts.all(), key=lambda c: c.created_at):
            enabled = context.state == ActivityContext.State.ENABLED
            if context.type == ActivityContext.Type.RENTAL:
                missing = _rental_missing(company.id, location.id, enabled)
            else:
                missing = ["module_not_available"]
            contexts.append(
                {
                    "id": str(context.id),
                    "type": context.type,
                    "ready": not missing,
                    "missing": missing,
                }
            )
        location_missing = []
        if not location.timezone:
            location_missing.append("no_timezone")
        if not contexts:
            location_missing.append("no_context")
        result_locations.append(
            {
                "id": str(location.id),
                "name": location.name,
                "missing": location_missing,
                "contexts": contexts,
            }
        )
    base_ready = all(base.values())
    first_operation_ready = base_ready and any(
        not loc["missing"] and any(c["ready"] for c in loc["contexts"]) for loc in result_locations
    )
    return {
        "company": {"items": base, "ready": base_ready},
        "locations": result_locations,
        # «Complete the initial setup» is available when at least one context is ready (§ 22.1.12).
        "first_operation_ready": first_operation_ready,
    }
