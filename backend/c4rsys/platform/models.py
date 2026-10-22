from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.db.models import Q
from django.utils import timezone

from c4rsys.platform.ids import uuid7


class PublicIdRegistry(models.Model):
    """Every readable ID ever issued. Guarantees an ID is never reused (ТЗ § 2.6.1)."""

    public_id = models.CharField(max_length=40, primary_key=True)
    entity_type = models.CharField(max_length=40)
    entity_id = models.UUIDField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "public_id_registry"
        constraints = [
            models.UniqueConstraint(
                fields=["entity_type", "entity_id"], name="public_id_registry_entity_uniq"
            ),
        ]

    def __str__(self) -> str:
        return self.public_id


class AuditEvent(models.Model):
    """Mandatory journal (ТЗ § 24.4.5). Append-only: a database trigger rejects UPDATE/DELETE.

    User and company are stored as plain UUIDs, not foreign keys: the journal outlives
    deactivation of accounts and employment (§ 23.10.1, § 24.4.6).
    """

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    occurred_at = models.DateTimeField(default=timezone.now)
    company_id = models.UUIDField(null=True, blank=True)
    actor_user_id = models.UUIDField(null=True, blank=True)
    actor_employment_id = models.UUIDField(null=True, blank=True)
    system_process = models.CharField(max_length=64, blank=True, default="")
    session_id = models.UUIDField(null=True, blank=True)
    request_id = models.CharField(max_length=64, blank=True, default="")
    action = models.CharField(max_length=100)
    object_type = models.CharField(max_length=60)
    object_id = models.CharField(max_length=64)
    before = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder)
    after = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder)
    reason = models.TextField(blank=True, default="")
    result = models.CharField(max_length=20, default="success")
    idempotency_key = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        db_table = "audit_event"
        indexes = [
            models.Index(fields=["company_id", "occurred_at"], name="audit_event_company_time_idx"),
            models.Index(fields=["object_type", "object_id"], name="audit_event_object_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(actor_user_id__isnull=False) | ~Q(system_process=""),
                name="audit_event_has_actor",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.action} {self.object_type}:{self.object_id}"


class IdempotencyRecord(models.Model):
    """Stored result of a completed command for a repeat of the same request (A07).

    The record is written in the same transaction as the command. If the command is not posted,
    the record does not exist either, so a later retry with the same key executes once.
    """

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    user_id = models.UUIDField()
    company_id = models.UUIDField(null=True, blank=True)
    key = models.CharField(max_length=200)
    command = models.CharField(max_length=100)
    request_hash = models.CharField(max_length=64)
    response_status = models.PositiveSmallIntegerField(default=0)
    response_body = models.JSONField(default=dict, encoder=DjangoJSONEncoder)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "idempotency_record"
        constraints = [
            models.UniqueConstraint(
                fields=["user_id", "company_id", "key"],
                name="idempotency_record_scope_key_uniq",
                nulls_distinct=False,
            ),
        ]

    def __str__(self) -> str:
        return f"{self.command}:{self.key}"
