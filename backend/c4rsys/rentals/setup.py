"""The setup wizard: its sections and the final readiness check (ТЗ § 22.1).

Readiness is assessed for the company, then separately for each location and each of its
contexts; an unfinished context does not block another (§ 22.1.12).
* Rental is ready by one of its ways of work: vehicle rental — a current tariff and a suitable
  vehicle (own or managed, bound to the location, allowed for rental, with that tariff), or a
  standalone additional service available to the location (§ 22.1.8–22.1.9).
* Own service is ready with an active service work (§ 22.1.8, § 22.1.12).
* A storage context is set up even empty (§ 22.1.9).
A section of the wizard is done by its own rule; employees and documents by an explicit answer
or by their settings (§ 22.1.10–22.1.11). Employees, cash, documents are not conditions of
completing the setup (§ 22.1.12).
"""

from typing import Any
from uuid import UUID

from c4rsys.companies import locations
from c4rsys.companies.models import ActivityContext, Company
from c4rsys.fleet import services as fleet
from c4rsys.rentals import tariffs


def _rental_missing(company_id: UUID, location_id: UUID, enabled: bool) -> list[str]:
    from c4rsys.rentals import extras

    if not enabled:
        return ["context_disabled"]
    if extras.at_location(company_id, location_id).exists():
        return []  # the way of standalone additional services (§ 22.1.8)
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
            elif not enabled:
                missing = ["context_disabled"]
            elif context.type == ActivityContext.Type.SERVICE:
                missing = [] if _has_service_work(company.id) else ["no_service_work"]
            elif context.type == ActivityContext.Type.WAREHOUSE:
                missing = []
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
        "sections": sections(company, result_locations),
        "choices": dict(company.setup_choices),
    }


def _has_service_work(company_id: UUID) -> bool:
    from c4rsys.service import catalog

    return catalog.has_active_work(company_id, company_id)


def sections(company: Company, result_locations: list[dict[str, Any]]) -> dict[str, bool]:
    """Completion of each section of the wizard (§ 22.1.4–22.1.12)."""
    from c4rsys.companies.models import Employment, Invitation

    contexts = [c for loc in result_locations for c in loc["contexts"]]
    types = {c["type"] for c in contexts}
    rental = [c for c in contexts if c["type"] == "rental"]
    catalog_ok = True
    if rental:
        catalog_ok = catalog_ok and any(
            "no_tariff" not in c["missing"] and "context_disabled" not in c["missing"]
            for c in rental
        )
    if "service" in types:
        catalog_ok = catalog_ok and _has_service_work(company.id)
    vehicles_ok = not rental or any(c["ready"] for c in rental)
    staff = (
        Employment.objects.filter(company=company).count() > 1
        or Invitation.objects.filter(company=company).exists()
    )
    choices = company.setup_choices or {}
    return {
        "profile": bool(company.display_name and company.timezone),
        "location": any(not loc["missing"] and loc["contexts"] for loc in result_locations),
        "finance": company.main_currency_id is not None,
        "catalog": bool(types) and catalog_ok,
        "vehicles": bool(types) and vehicles_ok,
        "employees": staff or choices.get("employees") == "alone",
        "documents": choices.get("documents") == "none" or _document_rules(company.id),
        "check": any(c["ready"] for c in contexts),
    }


def _document_rules(company_id: UUID) -> bool:
    from c4rsys.documents import requirements

    return (
        bool(requirements.of_company(company_id))
        or requirements.service_act(company_id).act_required
    )
