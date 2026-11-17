"""Tags in the single search line: by a part of the name. The tags of the company and the user's
own personal ones; the archived are not found."""

from uuid import UUID

from c4rsys.platform.commands import Actor
from c4rsys.platform.search import Hit, Query, exact_text
from c4rsys.tags import services


def provider(actor: Actor, company_id: UUID, query: Query, limit: int) -> list[Hit]:
    found = services.visible(company_id, actor.user_id).filter(
        archived_at__isnull=True, name_key__contains=query.lowered
    )
    return [
        Hit(
            "tag",
            str(tag.id),
            tag.name,
            exact=exact_text(tag.name, query),
            extra={"color": tag.color, "personal": tag.owner_user_id is not None},
        )
        for tag in found.order_by("name_key")[: limit * 2]
    ]
