"""The links of client and counterparty cards with a personal profile, for the merge and the
split of accounts by the support (ТЗ § 24.1.8; c4rsys.platform.userlinks). A company keeps one
card per profile (§ 6.2.10): two linked cards of one company are a conflict."""

from typing import Any
from uuid import UUID

from c4rsys.parties.models import ClientCard, Counterparty, ProfileLink
from c4rsys.platform.userlinks import UserLink

MODULE = "parties"


def _company(item: Any) -> dict[str, Any]:
    return {"company": item.company.public_id, "company_name": item.company.display_name}


def _name(card: Any) -> str:
    return str(card.company_name or f"{card.last_name} {card.first_name}".strip())


class PartyLinks:
    module = MODULE

    def links(self, user_id: UUID) -> list[UserLink]:
        result: list[UserLink] = []
        for card in ClientCard.objects.select_related("company").filter(linked_user_id=user_id):
            result.append(
                UserLink(
                    MODULE,
                    "client_card",
                    card.id,
                    {**_company(card), "card": _name(card)},
                    f"client_card:{card.company_id}",
                )
            )
        for cp in Counterparty.objects.select_related("company").filter(linked_user_id=user_id):
            result.append(
                UserLink(
                    MODULE,
                    "counterparty",
                    cp.id,
                    {**_company(cp), "card": _name(cp)},
                    f"counterparty:{cp.company_id}",
                )
            )
        for link in ProfileLink.objects.select_related("company").filter(target_user_id=user_id):
            result.append(
                UserLink(MODULE, "profile_link", link.id, {**_company(link), "state": link.state})
            )
        return result

    def move(self, kind: str, record_ids: list[UUID], source: UUID, target: UUID) -> int:
        model: Any
        field = "linked_user_id"
        if kind == "client_card":
            model = ClientCard
        elif kind == "counterparty":
            model = Counterparty
        elif kind == "profile_link":
            model, field = ProfileLink, "target_user_id"
        else:
            return 0
        ids = list(
            model.objects.select_for_update()
            .filter(pk__in=record_ids, **{field: source})
            .values_list("id", flat=True)
        )
        return int(model.objects.filter(pk__in=ids).update(**{field: target}))
