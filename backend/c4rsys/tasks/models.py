"""Work tasks of a company (ТЗ гл. 19, § 23.9; customer's decisions З-1…З-11 of 29.10.2026).

* A task belongs to one company and to the whole company or one location; a main object, when
  given, bounds the scope: a task is never wider than its object (§ 19.1.7).
* One executor or the common queue; assignment, taking, return and reassignment stay in the
  history (§ 19.1.6). Having an executor and being overdue are not states (§ 19.1.5).
* A done or cancelled task is not opened again (§ 19.2.1).
* The deadline is a moment; without a time it is the end of the chosen day in the zone of the
  task's location or of the company (§ 19.2.2).
"""

from django.db import models
from django.db.models import Q
from django.utils.timezone import now

from c4rsys.companies.models import Company, Employment, Location
from c4rsys.platform.ids import uuid7

ACTIVE_STATES = ("open", "in_progress", "waiting", "paused")


class Task(models.Model):
    class State(models.TextChoices):
        OPEN = "open"
        IN_PROGRESS = "in_progress"
        WAITING = "waiting"  # awaiting an answer, with what it waits for (§ 19.1.4)
        PAUSED = "paused"  # suspended with a reason (§ 19.1.4)
        DONE = "done"
        CANCELLED = "cancelled"

    class Priority(models.TextChoices):
        LOW = "low"
        NORMAL = "normal"
        HIGH = "high"
        CRITICAL = "critical"

    class Source(models.TextChoices):
        MANUAL = "manual"
        SYSTEM = "system"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    number = models.PositiveIntegerField()
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True, default="")
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.NORMAL)
    state = models.CharField(max_length=15, choices=State.choices, default=State.OPEN)
    # What a waiting task waits for, or why it is paused (§ 19.1.4).
    state_note = models.CharField(max_length=1000, blank=True, default="")
    # None — the whole company (§ 19.1.7).
    location = models.ForeignKey(
        Location, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    # None — the common queue (§ 19.1.2, § 19.1.6).
    assignee = models.ForeignKey(
        Employment, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.MANUAL)
    # A system task keeps its event and condition (§ 19.1.8).
    system_event = models.CharField(max_length=60, blank=True, default="")
    system_condition = models.CharField(max_length=500, blank=True, default="")
    object_type = models.CharField(max_length=20, blank=True, default="")
    object_id = models.UUIDField(null=True, blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    due_has_time = models.BooleanField(default=False)
    created_by_user_id = models.UUIDField(null=True, blank=True)
    created_by_employment = models.ForeignKey(
        Employment, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by_user_id = models.UUIDField(null=True, blank=True)
    # § 19.4.4: «all items must be done for completion».
    checklist_required = models.BooleanField(default=False)
    group = models.ForeignKey(
        "tasks.TaskGroup", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    # An instance of a repeating task and its date (§ 19.4.1).
    series = models.ForeignKey(
        "tasks.TaskSeries", on_delete=models.PROTECT, null=True, blank=True, related_name="tasks"
    )
    series_on = models.DateField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=1000, blank=True, default="")
    created_at = models.DateTimeField(default=now)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "task"
        constraints = [
            models.UniqueConstraint(fields=["company", "id"], name="task_company_id_uniq"),
            models.UniqueConstraint(fields=["company", "number"], name="task_number_uniq"),
            models.CheckConstraint(condition=~Q(title=""), name="task_title_not_empty"),
            models.CheckConstraint(
                condition=Q(object_type="", object_id__isnull=True)
                | (~Q(object_type="") & Q(object_id__isnull=False)),
                name="task_object_pair",
            ),
            models.CheckConstraint(
                condition=Q(state__in=["done", "cancelled"], closed_at__isnull=False)
                | (~Q(state__in=["done", "cancelled"]) & Q(closed_at__isnull=True)),
                name="task_closed_when_final",
            ),
            models.UniqueConstraint(
                fields=["series", "series_on"],
                condition=Q(series__isnull=False),
                name="task_series_one_per_date",
            ),
            models.CheckConstraint(
                condition=Q(series__isnull=True) | Q(series_on__isnull=False),
                name="task_series_has_date",
            ),
            models.CheckConstraint(
                condition=Q(source="manual", system_event="")
                | Q(source="system") & ~Q(system_event=""),
                name="task_system_has_event",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "state"], name="task_company_state_idx"),
            models.Index(fields=["company", "object_type", "object_id"], name="task_object_idx"),
        ]

    def __str__(self) -> str:
        return f"task {self.number}"


class TaskLink(models.Model):
    """An additional link of a task to an object; the main object stays on the task (§ 19.1.7)."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    task = models.ForeignKey(Task, on_delete=models.PROTECT, related_name="links")
    object_type = models.CharField(max_length=20)
    object_id = models.UUIDField()
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "task_link"
        constraints = [
            models.UniqueConstraint(
                fields=["task", "object_type", "object_id"], name="task_link_uniq"
            ),
        ]
        indexes = [
            models.Index(fields=["company", "object_type", "object_id"], name="task_link_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.task_id}->{self.object_type}:{self.object_id}"


class TaskComment(models.Model):
    """A comment, optionally with an attached file (§ 19.1.8; decision З-10). Not changed."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    task = models.ForeignKey(Task, on_delete=models.PROTECT, related_name="comments")
    text = models.TextField(blank=True, default="")
    file = models.ForeignKey(
        "files.StoredFile", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    file_name = models.CharField(max_length=255, blank=True, default="")
    author_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "task_comment"
        constraints = [
            models.CheckConstraint(
                condition=~Q(text="") | Q(file__isnull=False), name="task_comment_not_empty"
            ),
        ]

    def __str__(self) -> str:
        return f"comment {self.id}"


class TaskPolicy(models.Model):
    """The company's rules of § 19.1.6 and § 19.2.2 (decision З-2): by default the executor may
    return a task to the queue and may not change its deadline."""

    company = models.OneToOneField(Company, on_delete=models.PROTECT, related_name="+")
    executor_may_release = models.BooleanField(default=True)
    executor_may_change_due = models.BooleanField(default=False)
    updated_by_user_id = models.UUIDField()
    updated_at = models.DateTimeField(default=now)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "task_policy"

    def __str__(self) -> str:
        return f"task policy {self.company_id}"


class TaskChecklistItem(models.Model):
    """An item of the checklist of a task: no executor or deadline of its own; who and when
    changed or marked it is kept (§ 19.4.4)."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    task = models.ForeignKey(Task, on_delete=models.PROTECT, related_name="checklist")
    position = models.PositiveIntegerField()
    text = models.CharField(max_length=500)
    done = models.BooleanField(default=False)
    done_at = models.DateTimeField(null=True, blank=True)
    done_by_user_id = models.UUIDField(null=True, blank=True)
    updated_at = models.DateTimeField(default=now)
    updated_by_user_id = models.UUIDField()

    class Meta:
        db_table = "task_checklist_item"
        constraints = [
            models.CheckConstraint(condition=~Q(text=""), name="task_checklist_text_not_empty"),
            models.CheckConstraint(
                condition=Q(done=False) | Q(done_at__isnull=False, done_by_user_id__isnull=False),
                name="task_checklist_done_has_author",
            ),
        ]

    def __str__(self) -> str:
        return self.text


class TaskGroup(models.Model):
    """A group of tasks of the company by a theme; it does not widen access (§ 19.4.5)."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "task_group"
        constraints = [
            models.UniqueConstraint(fields=["company", "id"], name="task_group_company_id_uniq"),
            models.CheckConstraint(condition=~Q(name=""), name="task_group_name_not_empty"),
        ]

    def __str__(self) -> str:
        return self.name


class TaskTag(models.Model):
    """A tag of the company; it changes no access, executor, deadline or state (§ 19.4.7)."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    name = models.CharField(max_length=100)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "task_tag"
        constraints = [
            models.UniqueConstraint(fields=["company", "id"], name="task_tag_company_id_uniq"),
            models.UniqueConstraint(fields=["company", "name"], name="task_tag_name_uniq"),
            models.CheckConstraint(condition=~Q(name=""), name="task_tag_name_not_empty"),
        ]

    def __str__(self) -> str:
        return self.name


class TaskTagLink(models.Model):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    task = models.ForeignKey(Task, on_delete=models.PROTECT, related_name="tag_links")
    tag = models.ForeignKey(TaskTag, on_delete=models.PROTECT, related_name="links")

    class Meta:
        db_table = "task_tag_link"
        constraints = [
            models.UniqueConstraint(fields=["task", "tag"], name="task_tag_link_uniq"),
        ]

    def __str__(self) -> str:
        return f"{self.task_id}#{self.tag_id}"


class TaskDependency(models.Model):
    """«Start after completion»: the task waits for its predecessor (§ 19.4.6)."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    task = models.ForeignKey(Task, on_delete=models.PROTECT, related_name="predecessor_links")
    predecessor = models.ForeignKey(Task, on_delete=models.PROTECT, related_name="successor_links")
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "task_dependency"
        constraints = [
            models.UniqueConstraint(fields=["task", "predecessor"], name="task_dependency_uniq"),
            models.CheckConstraint(
                condition=~Q(task=models.F("predecessor")), name="task_dependency_not_self"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.predecessor_id}->{self.task_id}"


class TaskSeries(models.Model):
    """A repeating task: daily, on chosen weekdays or monthly, until an optional end date; each
    repetition is a task of its own (§ 19.4.1–19.4.2). The fields of the next tasks are kept
    here; done tasks are not rewritten by a change."""

    class Frequency(models.TextChoices):
        DAILY = "daily"
        WEEKLY = "weekly"
        MONTHLY = "monthly"

    class State(models.TextChoices):
        ACTIVE = "active"
        PAUSED = "paused"
        STOPPED = "stopped"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    frequency = models.CharField(max_length=10, choices=Frequency.choices)
    # ISO weekdays 1–7 for «weekly».
    weekdays = models.JSONField(default=list, blank=True)
    # The day of the month of the start: a missing day takes the last one (§ 19.4.1).
    day_of_month = models.PositiveSmallIntegerField(null=True, blank=True)
    starts_on = models.DateField()
    ends_on = models.DateField(null=True, blank=True)
    next_on = models.DateField(null=True, blank=True)
    state = models.CharField(max_length=10, choices=State.choices, default=State.ACTIVE)
    # The fields of the next tasks.
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True, default="")
    priority = models.CharField(max_length=10, default="normal")
    location = models.ForeignKey(
        Location, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    assignee = models.ForeignKey(
        Employment, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    object_type = models.CharField(max_length=20, blank=True, default="")
    object_id = models.UUIDField(null=True, blank=True)
    due_time = models.TimeField(null=True, blank=True)
    checklist = models.JSONField(default=list, blank=True)
    checklist_required = models.BooleanField(default=False)
    group = models.ForeignKey(
        TaskGroup, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    tag_ids = models.JSONField(default=list, blank=True)
    created_by_user_id = models.UUIDField()
    created_by_employment = models.ForeignKey(
        Employment, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(default=now)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "task_series"
        constraints = [
            models.UniqueConstraint(fields=["company", "id"], name="task_series_company_id_uniq"),
            models.CheckConstraint(
                condition=Q(ends_on__isnull=True) | Q(ends_on__gte=models.F("starts_on")),
                name="task_series_ends_after_start",
            ),
        ]

    def __str__(self) -> str:
        return f"series {self.title}"
