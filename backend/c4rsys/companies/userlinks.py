"""The links of a personal account with companies, for the merge and the split of accounts by the
support (ТЗ § 24.1.8; c4rsys.platform.userlinks): places of work with their positions, pending
invitations, ownership shares and the dedicated owner positions. Pending share transfers block a
merge until they finish (PROPOSAL): their parties would change in the middle of the procedure.
"""

from typing import Any
from uuid import UUID

from django.db.models import Q

from c4rsys.companies.models import (
    Company,
    Employment,
    Invitation,
    OwnerSeat,
    OwnershipShare,
    ShareTransfer,
    ShareTransferApproval,
)
from c4rsys.platform.userlinks import UserLink

MODULE = "companies"


def _company(company: Company) -> dict[str, Any]:
    return {"company": company.public_id, "company_name": company.display_name}


class CompanyLinks:
    module = MODULE

    def links(self, user_id: UUID) -> list[UserLink]:
        result: list[UserLink] = []
        for e in (
            Employment.objects.select_related("company", "position")
            .filter(user_id=user_id)
            .order_by("created_at")
        ):
            result.append(
                UserLink(
                    MODULE,
                    "employment",
                    e.id,
                    {
                        **_company(e.company),
                        "state": e.state,
                        "position_code": e.position.code,
                        "position_name": e.position.name,
                        "is_top_authority": e.position.is_top_authority,
                    },
                    "" if e.state == Employment.State.TERMINATED else f"employment:{e.company_id}",
                )
            )
        for i in Invitation.objects.select_related("company").filter(
            target_user_id=user_id, state=Invitation.State.PENDING
        ):
            result.append(
                UserLink(
                    MODULE, "invitation", i.id, _company(i.company), f"employment:{i.company_id}"
                )
            )
        for s in (
            OwnershipShare.objects.select_related("company")
            .filter(owner_user_id=user_id)
            .order_by("valid_from")
        ):
            current = s.valid_to is None
            result.append(
                UserLink(
                    MODULE,
                    "share",
                    s.id,
                    {**_company(s.company), "percent": str(s.percent), "current": current},
                    f"share:{s.company_id}" if current else "",
                )
            )
        for seat in OwnerSeat.objects.select_related("company").filter(owner_user_id=user_id):
            ended = seat.state == OwnerSeat.State.ENDED
            result.append(
                UserLink(
                    MODULE,
                    "owner_seat",
                    seat.id,
                    {**_company(seat.company), "state": seat.state},
                    "" if ended else f"seat:{seat.company_id}",
                )
            )
        transfers = ShareTransfer.objects.select_related("company").filter(
            Q(sender_user_id=user_id) | Q(recipient_user_id=user_id) | Q(successor_user_id=user_id),
            state=ShareTransfer.State.PENDING,
        )
        for t in transfers:
            result.append(
                UserLink(MODULE, "share_transfer", t.id, _company(t.company), "", blocking=True)
            )
        approvals = ShareTransferApproval.objects.select_related("company").filter(
            party_user_id=user_id, state=ShareTransferApproval.State.PENDING
        )
        for a in approvals:
            result.append(
                UserLink(MODULE, "share_approval", a.id, _company(a.company), "", blocking=True)
            )
        return result

    def move(self, kind: str, record_ids: list[UUID], source: UUID, target: UUID) -> int:
        if kind == "employment":
            query: Any = Employment.objects.filter(pk__in=record_ids, user_id=source)
            field = "user_id"
        elif kind == "invitation":
            query = Invitation.objects.filter(pk__in=record_ids, target_user_id=source)
            field = "target_user_id"
        elif kind == "share":
            query = OwnershipShare.objects.filter(pk__in=record_ids, owner_user_id=source)
            field = "owner_user_id"
        elif kind == "owner_seat":
            query = OwnerSeat.objects.filter(pk__in=record_ids, owner_user_id=source)
            field = "owner_user_id"
        else:
            # Pending transfers are not moved: they block the merge.
            return 0
        ids = list(query.select_for_update().values_list("id", flat=True))
        return int(query.model.objects.filter(pk__in=ids).update(**{field: target}))
