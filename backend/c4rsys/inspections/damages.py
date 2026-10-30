"""Damages (ТЗ § 10.3.6, § 10.5, § 16.5.4).

* A damage is created only by saving an inspection (§ 10.5.2): its entries are kept in the draft
  and become damage records at the save. Nothing else creates one — not the vehicle card, a
  rental, a service or an import.
* After the save it is «actual». «Fixed» — only by finishing a service work with the damages
  chosen explicitly (§ 10.5.1, § 16.5.4). «Erroneous» — only by recognising the source
  inspection invalid (§ 10.6.3). A new inspection recording a damage at the same place after its
  fixing either returns the previous damage (it is «actual» again) or creates a new one — the
  employee chooses (§ 10.5.1). Every change is an event of the history; nothing is rewritten.
* The estimate is money with its currency; empty means not estimated, 0 is an estimate. A damage
  charged in the rental becomes a charge of the client linked to the damage, the inspection, the
  rental and the vehicle; a waiver is a discount up to 100 % that keeps the charge (§ 10.5.3).
  Outside a rental the estimate is only kept (§ 10.5.3–10.5.4).
* The rental is not closed while a new damage attributed to it has no estimate or no charge
  (§ 10.5.5); the return itself is not blocked.
* TODO(spec): the history of damages of the global vehicle is shown to its owner by § 8.3.4,
  § 8.3.6 and § 8.3.15; the sharing of history categories is not implemented yet, so the damages
  stay visible inside the company only.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from django.utils import timezone

from c4rsys.files import services as files
from c4rsys.finance import charges
from c4rsys.finance.models import ChargeEntry
from c4rsys.inspections import links
from c4rsys.inspections.models import (
    Damage,
    DamageEvent,
    DamageFile,
    Inspection,
    InspectionFile,
)
from c4rsys.orders import services as orders
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.platform.models import Currency

SOURCE = ChargeEntry.SourceType.DAMAGE
State = Damage.State
Event = DamageEvent.Kind
# § 10.3.6 (decision В-8.2): the starting set of schemes.
SCHEMES = (
    "sedan",
    "hatchback",
    "wagon",
    "suv",
    "coupe",
    "convertible",
    "pickup",
    "van",
    "motorcycle",
    "scooter",
    "truck",
    "bus",
)
MAX_ENTRIES = 50
MAX_PHOTOS = 20


# --- Entries of a draft ------------------------------------------------------------------------


def _text(item: dict[str, Any], key: str, limit: int, field: str) -> str:
    value = item.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > limit:
        raise errors.validation_error(field)
    return value.strip()


def _uuid(value: Any, field: str) -> UUID:
    try:
        return UUID(str(value))
    except ValueError as exc:
        raise errors.validation_error(field) from exc


def _coordinate(item: dict[str, Any], key: str, field: str) -> str | None:
    value = item.get(key)
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        raise errors.validation_error(field)
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise errors.validation_error(field) from exc
    if not number.is_finite() or not 0 <= number <= 1:
        raise errors.validation_error(field, code="ZONE_POINT_INVALID")
    return str(number.quantize(Decimal("0.0001")))


def parse_estimate(value: Any, field: str) -> tuple[Decimal, str] | None:
    """{"amount": "1500", "currency": "THB"}; empty — not estimated (§ 10.5.5)."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise errors.validation_error(field)
    raw = value.get("amount")
    if isinstance(raw, bool) or not isinstance(raw, str | int | float) or raw == "":
        raise errors.validation_error(f"{field}.amount", code="AMOUNT_INVALID")
    try:
        amount = Decimal(str(raw))
    except InvalidOperation as exc:
        raise errors.validation_error(f"{field}.amount", code="AMOUNT_INVALID") from exc
    if not amount.is_finite() or amount < 0 or amount >= Decimal(10) ** 15:
        raise errors.validation_error(f"{field}.amount", code="AMOUNT_INVALID")
    currency = value.get("currency")
    if not isinstance(currency, str) or not Currency.objects.filter(code=currency).exists():
        raise errors.validation_error(f"{field}.currency", code="CURRENCY_UNKNOWN")
    places = Currency.objects.get(code=currency).minor_units
    exponent = amount.as_tuple().exponent
    if isinstance(exponent, int) and -exponent > places:
        raise errors.validation_error(f"{field}.amount", code="AMOUNT_INVALID")
    return amount, currency


def parse_entries(raw: Any, application: str) -> list[dict[str, Any]]:
    """Damages being recorded in a draft, kept as data until the save."""
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_ENTRIES:
        raise errors.validation_error("damages")
    result = []
    for index, item in enumerate(raw):
        field = f"damages[{index}]"
        if not isinstance(item, dict):
            raise errors.validation_error(field)
        returns = item.get("returns_damage_id")
        photos = item.get("photo_file_ids") or []
        if not isinstance(photos, list) or len(photos) > MAX_PHOTOS:
            raise errors.validation_error(f"{field}.photo_file_ids")
        entry: dict[str, Any] = {
            "photo_file_ids": [str(_uuid(p, f"{field}.photo_file_ids")) for p in photos],
            "returns_damage_id": str(_uuid(returns, f"{field}.returns_damage_id"))
            if returns
            else None,
        }
        if returns:
            # The previous damage keeps its own facts; the return records the new photos.
            result.append(entry)
            continue
        scheme = _text(item, "zone_scheme", 30, f"{field}.zone_scheme")
        if scheme and scheme not in SCHEMES:
            raise errors.validation_error(f"{field}.zone_scheme", code="SCHEME_UNKNOWN")
        x = _coordinate(item, "zone_x", f"{field}.zone_x")
        y = _coordinate(item, "zone_y", f"{field}.zone_y")
        if bool(scheme) != (x is not None) or (x is None) != (y is None):
            raise errors.validation_error(f"{field}.zone_scheme", code="ZONE_POINT_INVALID")
        zone_text = _text(item, "zone_text", 300, f"{field}.zone_text")
        description = _text(item, "description", 2000, f"{field}.description")
        if not (description or zone_text or scheme):
            raise errors.validation_error(f"{field}.description", code="DAMAGE_NOT_DESCRIBED")
        estimate = parse_estimate(item.get("estimate"), f"{field}.estimate")
        attributed = item.get("attributed_to_rental")
        if application == Inspection.Application.RENTAL:
            # TODO(spec): whether a damage of a rental inspection is charged in the rental by
            # default is not stated; the employee chooses explicitly.
            if not isinstance(attributed, bool):
                raise errors.validation_error(
                    f"{field}.attributed_to_rental", code="CHOICE_REQUIRED"
                )
        elif attributed not in (None, False):
            raise errors.validation_error(
                f"{field}.attributed_to_rental", code="DAMAGE_NOT_IN_RENTAL"
            )
        entry.update(
            zone_scheme=scheme,
            zone_x=x,
            zone_y=y,
            zone_text=zone_text,
            description=description,
            estimate={"amount": str(estimate[0]), "currency": estimate[1]} if estimate else None,
            attributed_to_rental=bool(attributed),
        )
        result.append(entry)
    return result


# --- Recording at the save ---------------------------------------------------------------------


def _event(
    ctx: CommandContext,
    damage: Damage,
    kind: str,
    before: str,
    *,
    at: datetime,
    odometer: Decimal | None = None,
    basis_type: str = "",
    basis_id: UUID | None = None,
    comment: str = "",
) -> DamageEvent:
    event = DamageEvent.objects.create(
        company_id=damage.company_id,
        damage=damage,
        kind=kind,
        state_before=before,
        state_after=damage.state,
        occurred_at=at,
        odometer_value=odometer,
        basis_type=basis_type,
        basis_id=basis_id,
        estimate_amount=damage.estimate_amount,
        estimate_currency=damage.estimate_currency_id or "",
        comment=comment,
        created_by_user_id=ctx.actor.user_id,
        created_at=timezone.now(),
    )
    ctx.audit(
        f"damage.{kind}",
        object_type="damage",
        object_id=damage.id,
        before={"state": before} if before else None,
        after={
            "state": damage.state,
            "basis_type": basis_type,
            "basis_id": str(basis_id) if basis_id else None,
            "estimate": str(damage.estimate_amount) if damage.estimate_amount is not None else None,
            "currency": damage.estimate_currency_id,
        },
        reason=comment,
    )
    return event


def _inspection_files(inspection: Inspection, ids: list[str], field: str) -> list[UUID]:
    """Photos chosen for a damage are photos of its inspection (§ 10.5.2)."""
    own = {
        str(f): f
        for f in InspectionFile.objects.filter(
            inspection=inspection, kind=InspectionFile.Kind.PHOTO
        ).values_list("file_id", flat=True)
    }
    missing = [i for i in ids if i not in own]
    if missing:
        raise errors.validation_error(field, code="DAMAGE_PHOTO_NOT_IN_INSPECTION")
    return [own[i] for i in ids]


def record(ctx: CommandContext, inspection: Inspection) -> list[Damage]:
    """Creates the damages of a saved inspection and returns previous ones it records again."""
    result = []
    card = inspection.vehicle_card
    for index, entry in enumerate(inspection.damages_draft or []):
        field = f"damages[{index}]"
        photos = _inspection_files(inspection, entry.get("photo_file_ids") or [], field)
        if entry.get("returns_damage_id"):
            damage = _return(ctx, inspection, UUID(entry["returns_damage_id"]), field)
            event = damage.events.order_by("-created_at").first()
        else:
            estimate = entry.get("estimate")
            damage = Damage.objects.create(
                company_id=inspection.company_id,
                vehicle_card_id=card.id,
                vehicle_id=card.vehicle_id,
                inspection=inspection,
                rental_id=inspection.rental_id,
                attributed_to_rental=bool(entry.get("attributed_to_rental")),
                performed_at=inspection.performed_at,
                odometer_value=inspection.odometer_value,
                odometer_unit=inspection.odometer_unit,
                zone_scheme=entry.get("zone_scheme") or "",
                zone_x=Decimal(entry["zone_x"]) if entry.get("zone_x") else None,
                zone_y=Decimal(entry["zone_y"]) if entry.get("zone_y") else None,
                zone_text=entry.get("zone_text") or "",
                description=entry.get("description") or "",
                state=State.ACTUAL,
                estimate_amount=Decimal(estimate["amount"]) if estimate else None,
                estimate_currency_id=estimate["currency"] if estimate else None,
                created_by_user_id=ctx.actor.user_id,
                created_at=timezone.now(),
            )
            event = _event(
                ctx,
                damage,
                Event.CREATED,
                "",
                at=inspection.performed_at,
                odometer=inspection.odometer_value,
                basis_type="inspection",
                basis_id=inspection.id,
            )
            _charge(ctx, damage, "damage_recorded")
        DamageFile.objects.bulk_create(
            [
                DamageFile(
                    company_id=damage.company_id,
                    damage=damage,
                    event=event,
                    file_id=f,
                    role=DamageFile.Role.SELECTED,
                )
                for f in photos
            ]
        )
        result.append(damage)
    return result


def _return(ctx: CommandContext, inspection: Inspection, damage_id: UUID, field: str) -> Damage:
    """§ 10.5.1: a fixed damage recorded again at the same place is actual again."""
    damage = (
        Damage.objects.select_for_update()
        .filter(company_id=inspection.company_id, pk=damage_id)
        .first()
    )
    if damage is None or damage.vehicle_card_id != inspection.vehicle_card_id:
        raise errors.validation_error(f"{field}.returns_damage_id", code="DAMAGE_UNKNOWN")
    if damage.state != State.FIXED:
        raise errors.business_rule(
            "DAMAGE_NOT_FIXED", field=f"{field}.returns_damage_id", state=damage.state
        )
    damage.state = State.ACTUAL
    damage.version += 1
    damage.save(update_fields=["state", "version"])
    _event(
        ctx,
        damage,
        Event.RETURNED,
        State.FIXED,
        at=inspection.performed_at,
        odometer=inspection.odometer_value,
        basis_type="inspection",
        basis_id=inspection.id,
    )
    return damage


# --- Estimate and charge (§ 10.5.3–10.5.5) -----------------------------------------------------


def _charge(ctx: CommandContext, damage: Damage, reason: str) -> None:
    """The estimate of a damage charged in the rental becomes the client's charge."""
    if not damage.attributed_to_rental or damage.rental_id is None:
        return
    if damage.estimate_amount is None or damage.estimate_currency_id is None:
        return
    rental = links.require_rental(damage.company_id, damage.rental_id)
    if rental.client_id is None:
        # TODO(spec): a rental without a client is not issued, so this does not happen yet.
        return
    orders.ensure_open(damage.company_id, rental.order_id)
    charges.set_charge(
        ctx,
        company_id=damage.company_id,
        order_id=rental.order_id,
        client_id=rental.client_id,
        source_type=SOURCE,
        source_id=damage.id,
        amount=damage.estimate_amount,
        currency=damage.estimate_currency_id,
        reason=reason,
        calculation={
            "damage": str(damage.id),
            "inspection": str(damage.inspection_id),
            "rental": str(damage.rental_id),
            "vehicle_card": str(damage.vehicle_card_id),
            "estimate": str(damage.estimate_amount),
        },
    )


def locked(company_id: UUID, damage_id: UUID, expected_version: int) -> Damage:
    damage = Damage.objects.select_for_update().filter(company_id=company_id, pk=damage_id).first()
    if damage is None:
        raise errors.not_found()
    if damage.version != expected_version:
        raise errors.version_conflict()
    return damage


def set_estimate(
    ctx: CommandContext, damage: Damage, estimate: tuple[Decimal, str] | None
) -> Damage:
    """Gives or changes the estimate; in a rental the charge follows it by an appended
    difference (§ 13.6.3). PROPOSAL (not confirmed): a given estimate may be changed later; the
    previous one stays in the history. An erroneous damage takes no estimate."""
    if damage.state == State.ERRONEOUS:
        raise errors.business_rule("DAMAGE_ERRONEOUS")
    if estimate is None:
        raise errors.validation_error("estimate", code="FIELD_REQUIRED")
    if damage.rental_id is not None and damage.attributed_to_rental:
        rental = links.require_rental(damage.company_id, damage.rental_id)
        orders.ensure_open(damage.company_id, rental.order_id)
        current = charges.charged_currency(SOURCE, damage.id)
        if current is not None and current != estimate[1]:
            raise errors.business_rule("DAMAGE_CURRENCY_CHANGED", field="estimate")
    damage.estimate_amount, damage.estimate_currency_id = estimate
    damage.version += 1
    damage.save(update_fields=["estimate_amount", "estimate_currency", "version"])
    _event(ctx, damage, Event.ESTIMATED, damage.state, at=timezone.now())
    _charge(ctx, damage, "damage_estimated")
    return damage


def charge_sources(rental_id: UUID) -> list[tuple[str, UUID]]:
    """Charges that go with the rental to another order (§ 12.3.7)."""
    return [
        (str(SOURCE), d)
        for d in Damage.objects.filter(rental_id=rental_id).values_list("id", flat=True)
    ]


def closing_blockers(rental_id: UUID) -> list[str]:
    """New damages attributed to the rental without an estimate or without its charge
    (§ 10.5.5). An explicit 0 is an estimate."""
    missing = []
    for damage in Damage.objects.filter(rental_id=rental_id, attributed_to_rental=True).exclude(
        state=State.ERRONEOUS
    ):
        if damage.estimate_amount is None:
            missing.append(str(damage.id))
        elif damage.estimate_amount > 0 and not charges.charge_ids_for_source(SOURCE, damage.id):
            missing.append(str(damage.id))
    return missing


# --- Fixing by a service work (§ 10.5.1, § 16.5.4) ---------------------------------------------


@dataclass(frozen=True)
class Fix:
    damage_id: UUID
    photo_file_ids: tuple[UUID, ...] = ()


def parse_fixes(raw: Any) -> list[Fix]:
    if raw in (None, []):
        return []
    if not isinstance(raw, list) or len(raw) > MAX_ENTRIES:
        raise errors.validation_error("fixed_damages")
    result = []
    for index, item in enumerate(raw):
        field = f"fixed_damages[{index}]"
        if not isinstance(item, dict):
            raise errors.validation_error(field)
        photos = item.get("photo_file_ids") or []
        if not isinstance(photos, list) or len(photos) > MAX_PHOTOS:
            raise errors.validation_error(f"{field}.photo_file_ids")
        result.append(
            Fix(
                _uuid(item.get("damage_id"), f"{field}.damage_id"),
                tuple(_uuid(p, f"{field}.photo_file_ids") for p in photos),
            )
        )
    if len({f.damage_id for f in result}) != len(result):
        raise errors.validation_error("fixed_damages", code="DAMAGE_REPEATED")
    return result


def fix_by_job(
    ctx: CommandContext,
    *,
    company_id: UUID,
    vehicle_card_id: UUID,
    job_id: UUID,
    at: datetime,
    odometer: Decimal | None,
    fixes: list[Fix],
) -> list[Damage]:
    """The damages the employee marks fixed when the work is done: the date, the odometer if
    known, the work as the basis and the photos of the result (§ 10.5.1)."""
    result = []
    for index, fix in enumerate(fixes):
        field = f"fixed_damages[{index}]"
        damage = (
            Damage.objects.select_for_update()
            .filter(company_id=company_id, pk=fix.damage_id)
            .first()
        )
        if damage is None or damage.vehicle_card_id != vehicle_card_id:
            raise errors.validation_error(f"{field}.damage_id", code="DAMAGE_UNKNOWN")
        if damage.state != State.ACTUAL:
            raise errors.business_rule(
                "DAMAGE_NOT_ACTUAL", field=f"{field}.damage_id", state=damage.state
            )
        for file_id in fix.photo_file_ids:
            files.usable(
                file_id,
                purpose=files.INSPECTION_PHOTO,
                user_id=ctx.actor.user_id,
                company_id=company_id,
            )
            files.attach_to_company(file_id, company_id)
        damage.state = State.FIXED
        damage.version += 1
        damage.save(update_fields=["state", "version"])
        event = _event(
            ctx,
            damage,
            Event.FIXED,
            State.ACTUAL,
            at=at,
            odometer=odometer,
            basis_type="service_job",
            basis_id=job_id,
        )
        DamageFile.objects.bulk_create(
            [
                DamageFile(
                    company_id=company_id,
                    damage=damage,
                    event=event,
                    file_id=f,
                    role=DamageFile.Role.FIX_RESULT,
                )
                for f in fix.photo_file_ids
            ]
        )
        result.append(damage)
    return result


# --- Invalid source inspection (§ 10.5.1, § 10.6.3) --------------------------------------------


def recognise_erroneous(ctx: CommandContext, inspection: Inspection, reason: str) -> None:
    """The damages created by an invalid inspection are erroneous; the record and its history
    stay, an erroneous one does not prove that the damage existed (§ 10.5.2).
    TODO(spec): what happens to the charge of an erroneous damage in a rental, and to a previous
    damage returned by the invalid inspection, is not stated; both are left as they are."""
    for damage in Damage.objects.select_for_update().filter(inspection=inspection):
        if damage.state == State.ERRONEOUS:
            continue
        before = damage.state
        damage.state = State.ERRONEOUS
        damage.version += 1
        damage.save(update_fields=["state", "version"])
        _event(
            ctx,
            damage,
            Event.ERRONEOUS,
            before,
            at=timezone.now(),
            basis_type="inspection",
            basis_id=inspection.id,
            comment=reason,
        )


# --- Reading -----------------------------------------------------------------------------------


def of_vehicle(company_id: UUID, card_id: UUID) -> list[Damage]:
    return list(
        Damage.objects.filter(company_id=company_id, vehicle_card_id=card_id).order_by(
            "-performed_at", "-created_at"
        )
    )


def _utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def payload(damage: Damage, *, history: bool = True) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": str(damage.id),
        "vehicle_card_id": str(damage.vehicle_card_id),
        "inspection_id": str(damage.inspection_id),
        "rental_id": str(damage.rental_id) if damage.rental_id else None,
        "attributed_to_rental": damage.attributed_to_rental,
        "performed_at": _utc(damage.performed_at),
        "odometer": str(damage.odometer_value) if damage.odometer_value is not None else None,
        "odometer_unit": damage.odometer_unit,
        "zone_scheme": damage.zone_scheme or None,
        "zone_x": str(damage.zone_x) if damage.zone_x is not None else None,
        "zone_y": str(damage.zone_y) if damage.zone_y is not None else None,
        "zone_text": damage.zone_text,
        "description": damage.description,
        "state": damage.state,
        "estimate": {
            "amount": str(damage.estimate_amount),
            "currency": damage.estimate_currency_id,
        }
        if damage.estimate_amount is not None
        else None,
        "charged": str(charges.current_charge(SOURCE, damage.id))
        if damage.attributed_to_rental
        else None,
        "version": damage.version,
    }
    if history:
        files_by_event: dict[UUID | None, list[dict[str, str]]] = {}
        for f in DamageFile.objects.filter(damage=damage).order_by("created_at", "id"):
            files_by_event.setdefault(f.event_id, []).append(
                {"file_id": str(f.file_id), "role": f.role}
            )
        data["history"] = [
            {
                "id": str(e.id),
                "kind": e.kind,
                "state_before": e.state_before or None,
                "state_after": e.state_after,
                "occurred_at": _utc(e.occurred_at),
                "odometer": str(e.odometer_value) if e.odometer_value is not None else None,
                "basis_type": e.basis_type or None,
                "basis_id": str(e.basis_id) if e.basis_id else None,
                "estimate": {"amount": str(e.estimate_amount), "currency": e.estimate_currency}
                if e.estimate_amount is not None
                else None,
                "comment": e.comment,
                "created_by_user_id": str(e.created_by_user_id),
                "created_at": _utc(e.created_at),
                "files": files_by_event.get(e.id, []),
            }
            for e in damage.events.order_by("created_at", "id")
        ]
    return data
