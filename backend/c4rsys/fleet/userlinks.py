"""The links of vehicles with a personal profile, for the merge and the split of accounts by the
support (ТЗ § 24.1.8; c4rsys.platform.userlinks): confirmed periods of ownership (the personal
garage), requests to the owner and the owner declared by a company. The answers keep the profile
that gave them (history of the action), they are not moved."""

from typing import Any
from uuid import UUID

from c4rsys.fleet.models import VehicleOwnerClaim, VehicleOwnerRequest, VehicleOwnership
from c4rsys.platform.userlinks import UserLink

MODULE = "fleet"


class VehicleLinks:
    module = MODULE

    def links(self, user_id: UUID) -> list[UserLink]:
        result: list[UserLink] = []
        for o in VehicleOwnership.objects.select_related("vehicle").filter(owner_user_id=user_id):
            current = o.ended_at is None
            result.append(
                UserLink(
                    MODULE,
                    "ownership",
                    o.id,
                    {"vehicle": o.vehicle.public_id, "current": current},
                    f"vehicle:{o.vehicle_id}" if current else "",
                )
            )
        for r in VehicleOwnerRequest.objects.select_related("vehicle", "company").filter(
            target_user_id=user_id
        ):
            result.append(
                UserLink(
                    MODULE,
                    "owner_request",
                    r.id,
                    {
                        "vehicle": r.vehicle.public_id,
                        "company": r.company.public_id,
                        "company_name": r.company.display_name,
                        "state": r.state,
                    },
                )
            )
        for c in VehicleOwnerClaim.objects.select_related("company").filter(owner_user_id=user_id):
            result.append(
                UserLink(
                    MODULE,
                    "owner_claim",
                    c.id,
                    {"company": c.company.public_id, "company_name": c.company.display_name},
                )
            )
        return result

    def move(self, kind: str, record_ids: list[UUID], source: UUID, target: UUID) -> int:
        model: Any
        field = "owner_user_id"
        if kind == "ownership":
            model = VehicleOwnership
        elif kind == "owner_request":
            model, field = VehicleOwnerRequest, "target_user_id"
        elif kind == "owner_claim":
            model = VehicleOwnerClaim
        else:
            return 0
        ids = list(
            model.objects.select_for_update()
            .filter(pk__in=record_ids, **{field: source})
            .values_list("id", flat=True)
        )
        return int(model.objects.filter(pk__in=ids).update(**{field: target}))
