"""Policy of inspections by kind and application (ТЗ § 10.3.1, § 10.3.4, § 14.3.1, § 14.3.6).

* Odometer and fuel of an issue and a return in a rental are always required (§ 14.3.1); whether
  the condition check of these is required is the inspection setting of the issue policy of the
  rental location (§ 14.3.6, module rentals): it is read through a registered provider and is not
  duplicated here.
* For the other kinds and applications the company sets whether the condition check is required
  and which readings are required. PROPOSAL (not confirmed): nothing is required by default —
  the non-blocking start of § 14.3.6.
* The required photos are those of the template (§ 10.3.5); they are checked only when the check
  is recorded as conducted — without the check missing photos do not prevent the save (§ 10.3.4).
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.utils import timezone

from c4rsys.inspections.models import Inspection, InspectionPolicy
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext

Kind = Inspection.Kind
Application = Inspection.Application

# (company_id, location_id) -> whether the condition check of a rental issue or return is
# required (registered by rentals: the issue policy, § 14.3.6).
RentalCheck = Callable[[UUID, UUID | None], bool]
_rental_check: list[RentalCheck] = []


def register_rental_check(provider: RentalCheck) -> None:
    _rental_check.append(provider)


def in_rental_card(kind: str, application: str) -> bool:
    """Issue and return of a rental, whose readings are entered in the rental card too."""
    return application == Application.RENTAL and kind in (Kind.ISSUE, Kind.RETURN)


@dataclass(frozen=True)
class Effective:
    check_required: bool
    odometer_required: bool
    fuel_required: bool
    cleanliness_required: bool


def effective(company_id: UUID, kind: str, application: str, location_id: UUID | None) -> Effective:
    stored = InspectionPolicy.objects.filter(
        company_id=company_id, kind=kind, application=application
    ).first()
    if in_rental_card(kind, application):
        check = any(p(company_id, location_id) for p in _rental_check)
        return Effective(check, True, True, bool(stored and stored.cleanliness_required))
    if stored is None:
        return Effective(False, False, False, False)
    return Effective(
        bool(stored.check_required),
        stored.odometer_required,
        stored.fuel_required,
        stored.cleanliness_required,
    )


def payload(company_id: UUID) -> list[dict[str, Any]]:
    stored = {
        (p.kind, p.application): p for p in InspectionPolicy.objects.filter(company_id=company_id)
    }
    result = []
    for kind in Kind.values:
        for application in Application.values:
            if kind == Kind.INTERMEDIATE and application == Application.STANDALONE:
                continue
            if kind == Kind.OUT_OF_RENTAL and application == Application.RENTAL:
                continue
            item = stored.get((kind, application))
            rental_card = in_rental_card(kind, application)
            result.append(
                {
                    "kind": kind,
                    "application": application,
                    # Null: set by the inspection setting of the issue policy (§ 14.3.6).
                    "check_required": None if rental_card else bool(item and item.check_required),
                    "odometer_required": rental_card or bool(item and item.odometer_required),
                    "fuel_required": rental_card or bool(item and item.fuel_required),
                    "cleanliness_required": bool(item and item.cleanliness_required),
                    "version": item.version if item else 0,
                }
            )
    return result


def _flag(values: dict[str, Any], key: str) -> bool:
    value = values.get(key)
    if not isinstance(value, bool):
        raise errors.validation_error(key)
    return value


def set_policy(
    ctx: CommandContext, company_id: UUID, kind: str, application: str, values: dict[str, Any]
) -> InspectionPolicy:
    if kind not in Kind.values:
        raise errors.validation_error("kind", code="CHOICE_REQUIRED")
    if application not in Application.values:
        raise errors.validation_error("application", code="CHOICE_REQUIRED")
    if (kind == Kind.INTERMEDIATE and application == Application.STANDALONE) or (
        kind == Kind.OUT_OF_RENTAL and application == Application.RENTAL
    ):
        raise errors.validation_error("application", code="INSPECTION_APPLICATION_INVALID")
    rental_card = in_rental_card(kind, application)
    check = None if rental_card else _flag(values, "check_required")
    odometer = True if rental_card else _flag(values, "odometer_required")
    fuel = True if rental_card else _flag(values, "fuel_required")
    cleanliness = _flag(values, "cleanliness_required")
    item = (
        InspectionPolicy.objects.select_for_update()
        .filter(company_id=company_id, kind=kind, application=application)
        .first()
    )
    expected = values.get("expected_version")
    current = item.version if item else 0
    if expected is not None and expected != current:
        raise errors.version_conflict()
    before = (
        {
            "check_required": item.check_required,
            "odometer_required": item.odometer_required,
            "fuel_required": item.fuel_required,
            "cleanliness_required": item.cleanliness_required,
        }
        if item
        else None
    )
    if item is None:
        item = InspectionPolicy(
            company_id=company_id, kind=kind, application=application, version=0
        )
    item.check_required = check
    item.odometer_required = odometer
    item.fuel_required = fuel
    item.cleanliness_required = cleanliness
    item.updated_by_user_id = ctx.actor.user_id
    item.updated_at = timezone.now()
    item.version += 1
    item.save()
    ctx.audit(
        "inspection.policy_changed",
        object_type="inspection_policy",
        object_id=item.id,
        before=before,
        after={
            "kind": kind,
            "application": application,
            "check_required": check,
            "odometer_required": odometer,
            "fuel_required": fuel,
            "cleanliness_required": cleanliness,
        },
    )
    return item
