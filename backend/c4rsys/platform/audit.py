"""Journal entries of reads and refusals, written outside a business command (ТЗ § 24.4.1,
§ 2.8.1, § 24.4.5).

A command journals inside its own transaction (c4rsys.platform.commands). A read that must be
journaled — the view or download of a document, an attempt to open an object that is not
available — writes its entry before the data is answered; if the entry cannot be written, the
read fails and nothing is shown.
"""

from typing import Any
from uuid import UUID

from c4rsys.platform.commands import Actor
from c4rsys.platform.models import AuditEvent


def record(
    action: str,
    *,
    object_type: str,
    object_id: str | UUID,
    company_id: UUID | None = None,
    actor: Actor | None = None,
    user_id: UUID | None = None,
    system_process: str = "",
    request_id: str = "",
    after: dict[str, Any] | None = None,
    result: str = "success",
) -> AuditEvent:
    actor_user = actor.user_id if actor else user_id
    return AuditEvent.objects.create(
        company_id=company_id if company_id is not None else (actor.company_id if actor else None),
        actor_user_id=actor_user,
        actor_employment_id=actor.employment_id if actor else None,
        session_id=actor.session_id if actor else None,
        system_process="" if actor_user else (system_process or "system"),
        request_id=request_id,
        action=action,
        object_type=object_type,
        object_id=str(object_id)[:64],
        after=after,
        result=result,
    )
