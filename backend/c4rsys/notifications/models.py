"""The notification centre (ТЗ гл. 20; customer's decisions З-1…З-11 of 29.10.2026).

* A notification belongs to a company and, when applicable, to a location; it has a kind
  («action required», «warning», «reminder», «information»), an event with its parameters for the
  text, the time and a link to its object (§ 20.1.3, § 20.1.5).
* While one condition lasts there is one active notification: a repetition updates it and stays
  in its history (§ 20.3.2). When the cause is removed it is done for every recipient; reading
  it does not end it (§ 20.3.3).
* Each recipient has an own mark of reading (§ 20.2.2).
"""

from django.db import models
from django.db.models import Q
from django.utils.timezone import now

from c4rsys.companies.models import Company, Employment, Location
from c4rsys.platform.ids import uuid7


class Notification(models.Model):
    class Kind(models.TextChoices):
        ACTION = "action"
        WARNING = "warning"
        REMINDER = "reminder"
        INFO = "info"

    class State(models.TextChoices):
        ACTIVE = "active"
        DONE = "done"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    location = models.ForeignKey(
        Location, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    kind = models.CharField(max_length=10, choices=Kind.choices)
    # The event of the catalogue (`task.overdue`) and the condition it is about: one active
    # notification per condition (§ 20.3.2).
    event = models.CharField(max_length=60)
    condition = models.CharField(max_length=200)
    params = models.JSONField(default=dict, blank=True)
    object_type = models.CharField(max_length=20, blank=True, default="")
    object_id = models.UUIDField(null=True, blank=True)
    state = models.CharField(max_length=10, choices=State.choices, default=State.ACTIVE)
    created_at = models.DateTimeField(default=now)
    last_at = models.DateTimeField(default=now)
    repeats = models.PositiveIntegerField(default=0)
    done_at = models.DateTimeField(null=True, blank=True)
    done_reason = models.CharField(max_length=60, blank=True, default="")

    class Meta:
        db_table = "notification"
        constraints = [
            models.UniqueConstraint(fields=["company", "id"], name="notification_company_id_uniq"),
            models.UniqueConstraint(
                fields=["company", "condition"],
                condition=Q(state="active"),
                name="notification_one_active_per_condition",
            ),
            models.CheckConstraint(
                condition=Q(state="active", done_at__isnull=True)
                | Q(state="done", done_at__isnull=False),
                name="notification_done_has_time",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "state"], name="notification_company_state_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.event} {self.condition}"


class NotificationRecipient(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    notification = models.ForeignKey(
        Notification, on_delete=models.PROTECT, related_name="recipients"
    )
    employment = models.ForeignKey(Employment, on_delete=models.PROTECT, related_name="+")
    read_at = models.DateTimeField(null=True, blank=True)
    added_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "notification_recipient"
        constraints = [
            models.UniqueConstraint(
                fields=["notification", "employment"], name="notification_recipient_uniq"
            ),
        ]
        indexes = [
            models.Index(fields=["employment", "read_at"], name="notification_recipient_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.notification_id}->{self.employment_id}"


class NotificationRepeat(models.Model):
    """A repeated reminder of an active notification (§ 20.3.2)."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    notification = models.ForeignKey(
        Notification, on_delete=models.PROTECT, related_name="repetitions"
    )
    at = models.DateTimeField(default=now)
    params = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "notification_repeat"

    def __str__(self) -> str:
        return f"{self.notification_id}@{self.at}"


class NotificationRule(models.Model):
    """The company's rule of a working event (§ 20.3.5–20.3.6; decisions З-7, З-8): on or off,
    the moment of the first notification, the repetition interval, chosen recipients among those
    with access and, where § 19.1.9 allows, an automatic task. Absent — the defaults."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    event = models.CharField(max_length=60)
    enabled = models.BooleanField(default=True)
    lead_minutes = models.PositiveIntegerField(default=0)
    repeat_minutes = models.PositiveIntegerField(default=0)
    recipient_ids = models.JSONField(default=list, blank=True)
    auto_task = models.BooleanField(default=False)
    updated_by_user_id = models.UUIDField()
    updated_at = models.DateTimeField(default=now)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "notification_rule"
        constraints = [
            models.UniqueConstraint(fields=["company", "event"], name="notification_rule_uniq"),
        ]

    def __str__(self) -> str:
        return f"{self.company_id}:{self.event}"


class JournalCursor(models.Model):
    """How far the journal of actions has been read for instant events (§ 20.3.5)."""

    name = models.CharField(max_length=40, primary_key=True)
    last_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "notification_journal_cursor"

    def __str__(self) -> str:
        return self.name
