"""Business command runner (A07, decision of 22 October 2026).

One completed command = one database transaction containing the business changes, the mandatory
journal entries and the idempotency record. If any part fails (including the journal), nothing is
saved and the caller receives OPERATION_NOT_POSTED. A repeat with the same Idempotency-Key and the
same request returns the stored result without executing the command again.
"""

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, transaction

from c4rsys.platform import errors
from c4rsys.platform.errors import ApiError
from c4rsys.platform.models import AuditEvent, IdempotencyRecord

logger = logging.getLogger(__name__)

MAX_IDEMPOTENCY_KEY_LENGTH = 200


@dataclass(frozen=True)
class Actor:
    """Who performs the command. Filled from the session and active company (T02, T03)."""

    user_id: UUID
    company_id: UUID | None = None
    employment_id: UUID | None = None
    session_id: UUID | None = None


@dataclass(frozen=True)
class CommandResult:
    status: int
    body: dict[str, Any]


@dataclass
class CommandContext:
    actor: Actor
    command: str
    idempotency_key: str
    request_id: str = ""
    _entries: list[AuditEvent] = field(default_factory=list)

    def audit(
        self,
        action: str,
        *,
        object_type: str,
        object_id: str | UUID,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        reason: str = "",
        company_id: UUID | None = None,
    ) -> None:
        """Queue a journal entry; it is written in the same transaction as the command.

        `company_id` is for a command that creates the company it is journaled in; otherwise the
        entry belongs to the active company of the actor.
        """
        self._entries.append(
            AuditEvent(
                company_id=company_id or self.actor.company_id,
                actor_user_id=self.actor.user_id,
                actor_employment_id=self.actor.employment_id,
                session_id=self.actor.session_id,
                request_id=self.request_id,
                action=action,
                object_type=object_type,
                object_id=str(object_id),
                before=before,
                after=after,
                reason=reason,
                idempotency_key=self.idempotency_key,
            )
        )

    @property
    def audit_entries(self) -> list[AuditEvent]:
        return list(self._entries)

    def audit_mark(self) -> int:
        """For a step run in a database savepoint: entries queued after the mark are dropped by
        `discard_audit` if the savepoint is rolled back, so the journal tells only what stayed."""
        return len(self._entries)

    def discard_audit(self, mark: int) -> None:
        del self._entries[mark:]


Handler = Callable[[CommandContext], CommandResult]


def request_hash(command: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"command": command, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        cls=DjangoJSONEncoder,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _replay(record: IdempotencyRecord, command: str, digest: str) -> CommandResult:
    if record.command != command or record.request_hash != digest:
        raise errors.idempotency_key_reused()
    return CommandResult(status=record.response_status, body=record.response_body)


def run_command(
    *,
    command: str,
    actor: Actor,
    idempotency_key: str,
    payload: dict[str, Any],
    handler: Handler,
    request_id: str = "",
) -> CommandResult:
    key = idempotency_key.strip()
    if not key or len(key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise errors.validation_error("Idempotency-Key", code="IDEMPOTENCY_KEY_REQUIRED")

    digest = request_hash(command, payload)
    scope: dict[str, Any] = {"user_id": actor.user_id, "company_id": actor.company_id, "key": key}

    try:
        with transaction.atomic():
            existing = IdempotencyRecord.objects.filter(**scope).first()
            if existing is not None:
                return _replay(existing, command, digest)

            record = IdempotencyRecord(command=command, request_hash=digest, **scope)
            try:
                # A concurrent request with the same key blocks here on the unique index until
                # the other transaction finishes; then it either replays or proceeds.
                with transaction.atomic():
                    record.save(force_insert=True)
            except IntegrityError:
                return _replay(IdempotencyRecord.objects.get(**scope), command, digest)

            context = CommandContext(
                actor=actor, command=command, idempotency_key=key, request_id=request_id
            )
            result = handler(context)

            entries = context.audit_entries
            if not entries:
                raise RuntimeError(f"Command {command} produced no journal entry")
            AuditEvent.objects.bulk_create(entries)

            record.response_status = result.status
            record.response_body = result.body
            record.save(update_fields=["response_status", "response_body"])
            return result
    except ApiError:
        raise
    except Exception as exc:
        logger.exception("Command %s not posted (request %s)", command, request_id)
        raise errors.operation_not_posted() from exc
