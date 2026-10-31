"""What the owner of a managed vehicle receives from the company (ТЗ § 8.3.4, § 8.3.6–8.3.7,
§ 8.3.10, § 8.3.15, § 10.5.2, § 10.6.1).

* Nothing before the management is confirmed by three «yes» (§ 8.3.1, § 8.3.4), even if the
  owner confirmed the ownership. History given while the management was confirmed is not taken
  back (§ 8.3.4, § 8.3.15).
* After the confirmation the owner sees the permitted history of this company — the records
  before the confirmation too — and the later ones (§ 8.3.6). Other companies' history and
  internal data are not disclosed.
* The company chooses the categories for this management (§ 8.3.15): operation periods and load;
  mileage and fuel; service works and materials; damages; inspections; and the financial ones of
  § 8.3.7 each apart — income, expenses, the result (income minus expenses), the settlements with
  the owner. Before the confirmation the owner sees the list of the offered categories, not their
  content. A change is kept with its moment: an extension opens more, a reduction stops passing
  new records, the received ones stay. After the management ends the updates stop; the received
  history stays (§ 8.3.15, § 8.3.17). A new owner gets nothing until he confirms (§ 8.3.10).
* The records come from the modules that keep them (`register_provider`); comments of the staff,
  client data, company documents and the general finance are never among them.

A record is visible to the owner when its moment is not later than the last moment at which the
management was confirmed for him and its category was switched on (PROPOSAL of the agent).
"""

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.utils import timezone

from c4rsys.fleet.models import (
    VehicleCard,
    VehicleDisclosureSetting,
    VehicleManagementConfirmation,
    VehicleOwnerRequest,
)
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext

CATEGORIES = (
    "operation_periods",
    "mileage_fuel",
    "service",
    "damages",
    "inspections",
    "finance_income",
    "finance_expenses",
    "finance_result",
    "settlements",
)
# (company_id, card_id, until or None) -> records of the category as dicts with "at" (an aware
# datetime); for money also "amount" and "currency". Registered by the module keeping them.
Provider = Callable[[UUID, UUID, datetime | None], list[dict[str, Any]]]
_providers: dict[str, list[Provider]] = {}


def register_provider(category: str, provider: Provider) -> None:
    if category not in CATEGORIES:
        raise ValueError(category)
    _providers.setdefault(category, []).append(provider)


# --- The confirmed management (§ 8.3.1) ----------------------------------------------------------


def start_confirmation(
    request: VehicleOwnerRequest, now: datetime
) -> VehicleManagementConfirmation:
    """The third «yes»: the management of this owner is confirmed from now."""
    VehicleManagementConfirmation.objects.filter(
        vehicle_card_id=request.vehicle_card_id, ended_at__isnull=True
    ).update(ended_at=now, end_reason="new_confirmation")
    return VehicleManagementConfirmation.objects.create(
        company_id=request.company_id,
        vehicle_card_id=request.vehicle_card_id,
        vehicle_id=request.vehicle_id,
        request=request,
        owner_user_id=request.target_user_id if request.target_company_id is None else None,
        owner_company_id=request.target_company_id,
        started_at=now,
    )


def end_confirmation(
    ctx: CommandContext | None, card: VehicleCard, reason: str, now: datetime
) -> VehicleManagementConfirmation | None:
    """The management stops being confirmed: a new declared owner, a change of the owner
    (§ 8.3.10), the end of the management (§ 8.3.17). What the owner got stays."""
    found = VehicleManagementConfirmation.objects.filter(
        vehicle_card_id=card.id, ended_at__isnull=True
    ).first()
    if found is None:
        return None
    found.ended_at = now
    found.end_reason = reason
    found.save(update_fields=["ended_at", "end_reason"])
    if ctx is not None:
        ctx.audit(
            "vehicle.management_confirmation_ended",
            object_type="vehicle_card",
            object_id=card.id,
            after={"confirmation": str(found.id), "reason": reason},
            company_id=card.company_id,
        )
    return found


def confirmed_now(card_id: UUID) -> VehicleManagementConfirmation | None:
    return VehicleManagementConfirmation.objects.filter(
        vehicle_card_id=card_id, ended_at__isnull=True
    ).first()


# --- Settings (§ 8.3.7, § 8.3.15) ----------------------------------------------------------------


def settings_of(card_id: UUID) -> list[VehicleDisclosureSetting]:
    return list(
        VehicleDisclosureSetting.objects.filter(vehicle_card_id=card_id).order_by(
            "effective_from", "created_at"
        )
    )


def current_categories(card_id: UUID, now: datetime | None = None) -> list[str]:
    moment = now or timezone.now()
    current = [s for s in settings_of(card_id) if s.effective_from <= moment]
    return list(current[-1].categories) if current else []


def set_categories(
    ctx: CommandContext,
    company_id: UUID,
    card_id: UUID,
    categories: list[str],
    effective_from: datetime | None,
) -> VehicleDisclosureSetting:
    from c4rsys.fleet import services as fleet

    card = fleet.lock_card(company_id, card_id)
    if card.relation != VehicleCard.Relation.MANAGED or card.state != VehicleCard.State.ACTIVE:
        raise errors.business_rule("VEHICLE_NOT_MANAGED")
    unknown = sorted(set(categories) - set(CATEGORIES))
    if unknown:
        raise errors.validation_error("categories", code="VALUE_UNKNOWN", values=unknown)
    now = timezone.now()
    moment = effective_from or now
    if moment < now:
        # The past is not rewritten: what was passed or not stays so (PROPOSAL).
        raise errors.validation_error("effective_from", code="DATETIME_IN_PAST")
    before = current_categories(card.id, now)
    setting = VehicleDisclosureSetting.objects.create(
        company_id=company_id,
        vehicle_card=card,
        categories=sorted(set(categories), key=CATEGORIES.index),
        effective_from=moment,
        created_by_user_id=ctx.actor.user_id,
    )
    ctx.audit(
        "vehicle.disclosure_changed",
        object_type="vehicle_card",
        object_id=card.id,
        before={"categories": before},
        after={"categories": setting.categories, "effective_from": moment},
    )
    return setting


def _enabled_intervals(card_id: UUID, category: str) -> list[tuple[datetime, datetime | None]]:
    result: list[tuple[datetime, datetime | None]] = []
    rows = settings_of(card_id)
    for i, s in enumerate(rows):
        if category not in s.categories:
            continue
        end = rows[i + 1].effective_from if i + 1 < len(rows) else None
        result.append((s.effective_from, end))
    return result


def visible_until(
    card_id: UUID,
    category: str,
    *,
    owner_user_id: UUID | None,
    owner_company_id: UUID | None,
    now: datetime,
) -> tuple[bool, datetime | None]:
    """(visible, until): the last moment the category was passed to this owner; until None —
    passed now, with all the records."""
    confirmations = VehicleManagementConfirmation.objects.filter(vehicle_card_id=card_id)
    confirmations = (
        confirmations.filter(owner_company_id=owner_company_id)
        if owner_company_id is not None
        else confirmations.filter(owner_user_id=owner_user_id)
    )
    best: datetime | None = None
    seen = False
    for c in confirmations:
        for start, end in _enabled_intervals(card_id, category):
            lo = max(start, c.started_at)
            hi_candidates = [x for x in (end, c.ended_at) if x is not None]
            hi = min(hi_candidates) if hi_candidates else None
            if lo > now or (hi is not None and hi <= lo):
                continue
            if hi is None or hi > now:
                return True, None
            seen = True
            best = hi if best is None or hi > best else best
    return seen, best


def _records(company_id: UUID, card_id: UUID, category: str, until: datetime | None) -> list[Any]:
    rows: list[dict[str, Any]] = []
    for provider in _providers.get(category, []):
        rows += provider(company_id, card_id, until)
    if until is not None:
        rows = [r for r in rows if r.get("at") is None or r["at"] <= until]
    rows.sort(key=lambda r: r["at"].timestamp() if r.get("at") else 0, reverse=True)
    return rows


def _json(row: dict[str, Any]) -> dict[str, Any]:
    return {
        k: v.isoformat() if isinstance(v, datetime) else str(v) if isinstance(v, Decimal) else v
        for k, v in row.items()
    }


def _totals(rows: list[dict[str, Any]]) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = {}
    for r in rows:
        currency = r.get("currency")
        if currency and r.get("amount") is not None:
            totals[currency] = totals.get(currency, Decimal(0)) + Decimal(str(r["amount"]))
    return totals


def for_owner(
    card: VehicleCard, *, owner_user_id: UUID | None, owner_company_id: UUID | None
) -> dict[str, Any]:
    """What the owner sees from one company: the offered categories, and the content of those
    passed to him."""
    now = timezone.now()
    offered = current_categories(card.id, now)
    confirmed = confirmed_now(card.id)
    mine = confirmed is not None and (
        confirmed.owner_company_id == owner_company_id
        if owner_company_id is not None
        else confirmed.owner_user_id == owner_user_id
    )
    categories: list[dict[str, Any]] = []
    for category in CATEGORIES:
        visible, until = visible_until(
            card.id,
            category,
            owner_user_id=owner_user_id,
            owner_company_id=owner_company_id,
            now=now,
        )
        if not visible:
            if category in offered:
                categories.append({"category": category, "passed": False})
            continue
        item: dict[str, Any] = {
            "category": category,
            "passed": True,
            "until": until.isoformat() if until else None,
        }
        if category == "finance_result":
            income = _totals(_records(card.company_id, card.id, "finance_income", until))
            expenses = _totals(_records(card.company_id, card.id, "finance_expenses", until))
            item["totals"] = [
                {
                    "currency": cur,
                    "income": str(income.get(cur, Decimal(0))),
                    "expenses": str(expenses.get(cur, Decimal(0))),
                    "result": str(income.get(cur, Decimal(0)) - expenses.get(cur, Decimal(0))),
                }
                for cur in sorted(set(income) | set(expenses))
            ]
        else:
            rows = _records(card.company_id, card.id, category, until)
            item["records"] = [_json(r) for r in rows]
            if category in ("finance_income", "finance_expenses", "settlements"):
                item["totals"] = [
                    {"currency": k, "amount": str(v)} for k, v in _totals(rows).items()
                ]
        categories.append(item)
    return {
        "company": {"public_id": card.company.public_id, "display_name": card.company.display_name},
        "confirmed": mine,
        "offered": offered,
        "categories": categories,
    }


def card_payload(card: VehicleCard) -> dict[str, Any]:
    """For the company: the settings, their history and whether the management is confirmed."""
    confirmed = confirmed_now(card.id)
    return {
        "categories": current_categories(card.id),
        "all": list(CATEGORIES),
        "confirmed_since": confirmed.started_at.isoformat() if confirmed else None,
        "history": [
            {
                "categories": s.categories,
                "effective_from": s.effective_from.isoformat(),
                "created_at": s.created_at.isoformat(),
            }
            for s in reversed(settings_of(card.id))
        ],
    }


def readings_for_owner(company_id: UUID, card_id: UUID, until: datetime | None) -> list[Any]:
    """The readings of the odometer with the accumulated mileage (§ 8.2.13)."""
    from c4rsys.fleet import odometer
    from c4rsys.fleet.models import OdometerReading

    query = OdometerReading.objects.filter(
        company_id=company_id, vehicle_card_id=card_id, replaced_by__isnull=True
    )
    if until is not None:
        query = query.filter(measured_at__lte=until)
    return [
        {
            "at": r.measured_at,
            "kind": "odometer",
            "odometer": str(r.value),
            "unit": r.unit,
            "accumulated": str(odometer.accumulated(r)),
        }
        for r in query.order_by("-measured_at")
    ]
