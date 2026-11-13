"""Who did what with a card of a vehicle (customer's decision of 13.11.2026): the entries of the
mandatory journal (§ 24.4.5) about the card, with the name of the author, newest first."""

from typing import Any

from c4rsys.accounts import services as accounts
from c4rsys.fleet.models import VehicleCard
from c4rsys.platform.models import AuditEvent

LIMIT = 300


def _subject(entry: AuditEvent) -> str:
    """The name of the document the entry is about, if it is about one."""
    if not entry.action.startswith("vehicle_document."):
        return ""
    for values in (entry.after, entry.before):
        if isinstance(values, dict) and isinstance(values.get("name"), str):
            return str(values["name"])
    return ""


def card_journal(card: VehicleCard) -> list[dict[str, Any]]:
    entries = list(
        AuditEvent.objects.filter(
            company_id=card.company_id,
            object_type="vehicle_card",
            object_id=str(card.id),
            result="success",
        ).order_by("-occurred_at", "-id")[:LIMIT]
    )
    names = accounts.names({e.actor_user_id for e in entries if e.actor_user_id})
    return [
        {
            "id": str(e.id),
            "at": e.occurred_at.isoformat(),
            "action": e.action,
            "author": names.get(e.actor_user_id, "") if e.actor_user_id else "",
            "subject": _subject(e),
        }
        for e in entries
    ]
