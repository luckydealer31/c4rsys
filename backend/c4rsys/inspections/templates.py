"""Templates of inspections (ТЗ § 10.3.5).

* Every company has two ready templates — cars and motorcycles — that it edits. The base
  composition is the same: odometer, fuel or charge, cleanliness, damages and photos.
* The base photo angles: front, rear, left and right sides, under and over the vehicle, the
  interior in front and behind, the odometer. Interior angles do not apply to a motorcycle
  (§ 10.3.5: «applicability depends on the transport and the template»).
* The company enables and disables positions, adds its own, sets their count and obligation.
  A change makes a new version; a saved inspection keeps the version it used.
* PROPOSAL (not confirmed by the customer): in the ready templates every applicable position is
  enabled, not required, one photo each — the non-blocking start of § 14.3.6.
"""

from typing import Any
from uuid import UUID

from django.utils import timezone

from c4rsys.fleet.models import VehicleCard
from c4rsys.inspections.models import InspectionTemplate, InspectionTemplateVersion
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext

BASE_POSITIONS = (
    "front",
    "rear",
    "left",
    "right",
    "under",
    "top",
    "interior_front",
    "interior_rear",
    "odometer",
)
INTERIOR = ("interior_front", "interior_rear")
Transport = InspectionTemplate.Transport
MAX_COUNT = 20
MAX_POSITIONS = 50
# TODO(spec): which template a truck, a bus or «other» use is not stated; the car template is
# offered by default and the employee may choose the other one.
MOTORCYCLE_TYPES = ("motorcycle", "moped")


def default_positions(transport: str) -> list[dict[str, Any]]:
    codes = [c for c in BASE_POSITIONS if not (transport == Transport.MOTORCYCLE and c in INTERIOR)]
    return [{"code": c, "enabled": True, "required": False, "count": 1} for c in codes]


def create_starting(company_id: UUID) -> None:
    """Called in the transaction that creates the company (§ 10.3.5: ready templates)."""
    for transport in Transport.values:
        template = InspectionTemplate.objects.create(company_id=company_id, transport=transport)
        InspectionTemplateVersion.objects.create(
            company_id=company_id,
            template=template,
            number=1,
            positions=default_positions(transport),
        )


def templates_of(company_id: UUID) -> list[InspectionTemplate]:
    return list(InspectionTemplate.objects.filter(company_id=company_id).order_by("transport"))


def current_version(template: InspectionTemplate) -> InspectionTemplateVersion:
    return InspectionTemplateVersion.objects.get(template=template, number=template.current_number)


def transport_of(card: VehicleCard) -> str:
    vehicle_type = card.vehicle.vehicle_type if card.vehicle_id else ""
    return Transport.MOTORCYCLE if vehicle_type in MOTORCYCLE_TYPES else Transport.CAR


def version_for(
    company_id: UUID, card: VehicleCard, template_id: UUID | None
) -> InspectionTemplateVersion | None:
    """The current version of the chosen template, else of the template for the vehicle."""
    query = InspectionTemplate.objects.filter(company_id=company_id)
    if template_id is not None:
        template = query.filter(pk=template_id).first()
        if template is None:
            raise errors.validation_error("template_id", code="TEMPLATE_UNKNOWN")
    else:
        template = query.filter(transport=transport_of(card)).first()
        if template is None:
            return None
    return current_version(template)


def payload(template: InspectionTemplate) -> dict[str, Any]:
    version = current_version(template)
    return {
        "id": str(template.id),
        "transport": template.transport,
        "version": template.current_number,
        "version_id": str(version.id),
        "positions": version.positions,
        "base_positions": [c["code"] for c in default_positions(template.transport)],
        "updated_at": version.created_at.isoformat(),
    }


def version_payload(version: InspectionTemplateVersion | None) -> dict[str, Any] | None:
    if version is None:
        return None
    return {
        "id": str(version.id),
        "template_id": str(version.template_id),
        "number": version.number,
        "positions": version.positions,
    }


def _flag(item: dict[str, Any], key: str, index: int) -> bool:
    value = item.get(key)
    if not isinstance(value, bool):
        raise errors.validation_error(f"positions[{index}].{key}")
    return value


def checked_positions(transport: str, raw: Any) -> list[dict[str, Any]]:
    """Base positions applicable to the transport and own ones with a name; a code once."""
    if not isinstance(raw, list) or not raw or len(raw) > MAX_POSITIONS:
        raise errors.validation_error("positions", code="POSITIONS_INVALID")
    base = {c["code"] for c in default_positions(transport)}
    seen: set[str] = set()
    result = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise errors.validation_error(f"positions[{index}]")
        code = item.get("code")
        if not isinstance(code, str) or not code.strip() or len(code) > 60:
            raise errors.validation_error(f"positions[{index}].code")
        code = code.strip()
        if code in seen:
            raise errors.validation_error(f"positions[{index}].code", code="POSITION_REPEATED")
        seen.add(code)
        count = item.get("count")
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_COUNT:
            raise errors.validation_error(f"positions[{index}].count")
        position: dict[str, Any] = {
            "code": code,
            "enabled": _flag(item, "enabled", index),
            "required": _flag(item, "required", index),
            "count": count,
        }
        if code not in base:
            if code in BASE_POSITIONS:  # a base angle that does not apply to this transport
                raise errors.validation_error(
                    f"positions[{index}].code", code="POSITION_NOT_APPLICABLE"
                )
            name = item.get("name")
            if not isinstance(name, str) or not name.strip() or len(name) > 100:
                raise errors.validation_error(f"positions[{index}].name", code="FIELD_REQUIRED")
            position["name"] = name.strip()
        if position["required"] and not position["enabled"]:
            raise errors.validation_error(
                f"positions[{index}].required", code="POSITION_REQUIRED_DISABLED"
            )
        result.append(position)
    return result


def change(
    ctx: CommandContext, company_id: UUID, template_id: UUID, raw: Any, expected_version: int
) -> InspectionTemplate:
    """A new version of the template; saved inspections keep theirs (§ 10.3.5)."""
    template = (
        InspectionTemplate.objects.select_for_update()
        .filter(company_id=company_id, pk=template_id)
        .first()
    )
    if template is None:
        raise errors.not_found()
    if template.current_number != expected_version:
        raise errors.version_conflict()
    positions = checked_positions(template.transport, raw)
    before = current_version(template).positions
    template.current_number += 1
    template.save(update_fields=["current_number"])
    InspectionTemplateVersion.objects.create(
        company_id=company_id,
        template=template,
        number=template.current_number,
        positions=positions,
        created_by_user_id=ctx.actor.user_id,
        created_at=timezone.now(),
    )
    ctx.audit(
        "inspection.template_changed",
        object_type="inspection_template",
        object_id=template.id,
        before={"version": template.current_number - 1, "positions": before},
        after={"version": template.current_number, "positions": positions},
    )
    return template
