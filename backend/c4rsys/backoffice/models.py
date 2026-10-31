"""The system staff of c4rsys and its internal interface (ТЗ гл. 24; block 11, parts П1–П4).

* System roles and permissions are separate from the roles and rights of companies and from
  employment (§ 24.1.1, § 24.1.6). A personal account alone gives no access.
* The system journal is kept apart from the journals of companies (§ 24.4.5); a database trigger
  rejects any change or deletion of its rows.
* Users and companies are stored as plain UUIDs where the record must outlive them (the journal,
  the links of a ticket); a block and a privileged session point to their target.
"""

from django.contrib.postgres.fields import ArrayField
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.db.models import Q
from django.utils import timezone

from c4rsys.accounts.models import UserAccount
from c4rsys.companies.models import Company
from c4rsys.platform.ids import uuid7


class SystemRole(models.Model):
    """A system set: the three starter sets of § 24.1.5 and sets made by a platform
    administrator with concrete permissions (§ 24.1.6)."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    code = models.CharField(max_length=60, unique=True)
    name = models.CharField(max_length=200, blank=True, default="")
    permissions = ArrayField(models.CharField(max_length=60), default=list)
    is_starter = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    created_by_user_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "system_role"

    def __str__(self) -> str:
        return self.code


class SystemStaffMember(models.Model):
    """A member of the system staff, tied to a personal account."""

    class State(models.TextChoices):
        ACTIVE = "active"
        REVOKED = "revoked"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    user = models.OneToOneField(UserAccount, on_delete=models.PROTECT, related_name="+")
    # The unit shown to a company in its journal instead of the person (§ 24.3.2).
    unit = models.CharField(max_length=100, blank=True, default="")
    state = models.CharField(max_length=10, choices=State.choices, default=State.ACTIVE)
    created_at = models.DateTimeField(default=timezone.now)
    created_by_user_id = models.UUIDField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "system_staff_member"

    def __str__(self) -> str:
        return f"staff {self.user_id}"


class SystemRoleAssignment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    member = models.ForeignKey(
        SystemStaffMember, on_delete=models.PROTECT, related_name="assignments"
    )
    role = models.ForeignKey(SystemRole, on_delete=models.PROTECT, related_name="+")
    granted_at = models.DateTimeField(default=timezone.now)
    granted_by_user_id = models.UUIDField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by_user_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "system_role_assignment"
        constraints = [
            models.UniqueConstraint(
                fields=["member", "role"],
                condition=Q(revoked_at__isnull=True),
                name="system_role_assignment_active_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.member_id}:{self.role_id}"


class SystemJournalEntry(models.Model):
    """Journal of every action of the system staff (§ 24.1.2, § 24.4.5). Append-only.

    For a privileged action the system roles, the session and the ticket or reason are kept.
    """

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    occurred_at = models.DateTimeField(default=timezone.now)
    actor_user_id = models.UUIDField(null=True, blank=True)
    staff_member_id = models.UUIDField(null=True, blank=True)
    system_process = models.CharField(max_length=64, blank=True, default="")
    roles = ArrayField(models.CharField(max_length=60), default=list, blank=True)
    session_id = models.UUIDField(null=True, blank=True)
    privileged_session_id = models.UUIDField(null=True, blank=True)
    ticket_id = models.UUIDField(null=True, blank=True)
    request_id = models.CharField(max_length=64, blank=True, default="")
    action = models.CharField(max_length=100)
    object_type = models.CharField(max_length=60)
    object_id = models.CharField(max_length=64)
    target_company_id = models.UUIDField(null=True, blank=True)
    target_user_id = models.UUIDField(null=True, blank=True)
    before = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder)
    after = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder)
    reason = models.TextField(blank=True, default="")
    result = models.CharField(max_length=20, default="success")
    idempotency_key = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        db_table = "system_journal_entry"
        indexes = [
            models.Index(fields=["occurred_at"], name="system_journal_time_idx"),
            models.Index(fields=["object_type", "object_id"], name="system_journal_object_idx"),
            models.Index(fields=["actor_user_id", "occurred_at"], name="system_journal_actor_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(actor_user_id__isnull=False) | ~Q(system_process=""),
                name="system_journal_has_actor",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.action} {self.object_type}:{self.object_id}"


class SystemCounter(models.Model):
    """Sequential numbers of the internal interface (tickets); taken in the command's
    transaction, so a rolled-back command does not consume a number."""

    kind = models.CharField(max_length=20, primary_key=True)
    last_value = models.BigIntegerField(default=0)

    class Meta:
        db_table = "system_counter"

    def __str__(self) -> str:
        return f"{self.kind}={self.last_value}"


class SupportTicket(models.Model):
    """A support ticket registered by hand from a message of an external channel (§ 24.1.3,
    § 24.1.7)."""

    class State(models.TextChoices):
        NEW = "new"
        IN_PROGRESS = "in_progress"
        WAITING = "waiting"
        CLOSED = "closed"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    number = models.BigIntegerField(unique=True)
    received_on = models.DateField()
    channel = models.CharField(max_length=20, default="email")
    applicant_name = models.CharField(max_length=200, blank=True, default="")
    applicant_contact = models.CharField(max_length=254, blank=True, default="")
    subject = models.CharField(max_length=300)
    description = models.TextField()
    state = models.CharField(max_length=20, choices=State.choices, default=State.NEW)
    assignee = models.ForeignKey(
        SystemStaffMember, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    result = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)
    created_by_user_id = models.UUIDField()
    closed_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "support_ticket"

    def __str__(self) -> str:
        return f"ticket {self.number}"


class TicketLink(models.Model):
    """An object the ticket is about: a company, a user or a disputed record."""

    class Kind(models.TextChoices):
        COMPANY = "company"
        USER = "user"
        RECORD = "record"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    ticket = models.ForeignKey(SupportTicket, on_delete=models.PROTECT, related_name="links")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    company_id = models.UUIDField(null=True, blank=True)
    user_id = models.UUIDField(null=True, blank=True)
    # A disputed record: its type and readable reference as given by the staff member.
    record_type = models.CharField(max_length=60, blank=True, default="")
    record_ref = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)
    created_by_user_id = models.UUIDField()

    class Meta:
        db_table = "support_ticket_link"

    def __str__(self) -> str:
        return f"{self.ticket_id}:{self.kind}"


class TicketComment(models.Model):
    """A comment or a recorded confirmation (§ 24.1.7). Not changed after it is written."""

    class Kind(models.TextChoices):
        COMMENT = "comment"
        CONFIRMATION = "confirmation"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    ticket = models.ForeignKey(SupportTicket, on_delete=models.PROTECT, related_name="comments")
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.COMMENT)
    text = models.TextField()
    author = models.ForeignKey(SystemStaffMember, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "support_ticket_comment"

    def __str__(self) -> str:
        return f"{self.ticket_id}:{self.kind}"


class Notice(models.TextChoices):
    """The notice to the company or the user after a privileged session or a block."""

    PENDING = "pending"  # to be sent when due
    DEFERRED = "deferred"  # held by a separate system permission (§ 24.2.2, § 24.3.2)
    SENT = "sent"
    NONE = "none"  # nobody is told (TODO(spec): a privileged session for a user)


class PrivilegedSession(models.Model):
    """Access of a staff member to the closed data of one company or one user (§ 24.3.1)."""

    class Target(models.TextChoices):
        COMPANY = "company"
        USER = "user"

    class EndReason(models.TextChoices):
        CLOSED = "closed"
        EXPIRED = "expired"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    member = models.ForeignKey(SystemStaffMember, on_delete=models.PROTECT, related_name="+")
    target_type = models.CharField(max_length=10, choices=Target.choices)
    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    user = models.ForeignKey(
        UserAccount, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    reason_category = models.CharField(max_length=40)
    reason = models.TextField()
    ticket = models.ForeignKey(
        SupportTicket, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    scopes = ArrayField(models.CharField(max_length=20), default=list)
    roles = ArrayField(models.CharField(max_length=60), default=list)
    unit = models.CharField(max_length=100, blank=True, default="")
    started_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    end_reason = models.CharField(max_length=10, choices=EndReason.choices, blank=True, default="")
    notice = models.CharField(max_length=10, choices=Notice.choices, default=Notice.PENDING)
    notice_deferred_by_user_id = models.UUIDField(null=True, blank=True)
    notice_deferral_reason = models.TextField(blank=True, default="")
    notified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "privileged_session"
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(target_type="company") & Q(company__isnull=False) & Q(user__isnull=True)
                )
                | (Q(target_type="user") & Q(user__isnull=False) & Q(company__isnull=True)),
                name="privileged_session_one_target",
            ),
            models.UniqueConstraint(
                fields=["member"],
                condition=Q(ended_at__isnull=True),
                name="privileged_session_one_open_per_member",
            ),
        ]

    def __str__(self) -> str:
        return f"privileged {self.id}"


class PrivilegedAccess(models.Model):
    """An opened section or object, or a download, inside a privileged session (§ 24.3.1)."""

    class Kind(models.TextChoices):
        VIEW = "view"
        DOWNLOAD = "download"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    session = models.ForeignKey(PrivilegedSession, on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.VIEW)
    section = models.CharField(max_length=40)
    object_id = models.CharField(max_length=64, blank=True, default="")
    at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "privileged_access"

    def __str__(self) -> str:
        return f"{self.session_id}:{self.section}"


class SystemBlock(models.Model):
    """A system block of a user or a company (§ 24.2.1–24.2.2)."""

    class Target(models.TextChoices):
        COMPANY = "company"
        USER = "user"

    class EndKind(models.TextChoices):
        MANUAL = "manual"
        EXPIRED = "expired"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    target_type = models.CharField(max_length=10, choices=Target.choices)
    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    user = models.ForeignKey(
        UserAccount, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    category = models.CharField(max_length=40)
    # Internal comment of the staff member; not shown outside the internal interface.
    comment = models.TextField()
    # The reason that may be disclosed to the user or the owners (§ 24.2.2).
    disclosed_reason = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField(null=True, blank=True)
    created_by_user_id = models.UUIDField()
    ended_at = models.DateTimeField(null=True, blank=True)
    end_kind = models.CharField(max_length=10, choices=EndKind.choices, blank=True, default="")
    end_reason = models.TextField(blank=True, default="")
    ended_by_user_id = models.UUIDField(null=True, blank=True)
    notice = models.CharField(max_length=10, choices=Notice.choices, default=Notice.PENDING)
    notice_deferred_by_user_id = models.UUIDField(null=True, blank=True)
    notice_deferral_reason = models.TextField(blank=True, default="")
    notified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "system_block"
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(target_type="company") & Q(company__isnull=False) & Q(user__isnull=True)
                )
                | (Q(target_type="user") & Q(user__isnull=False) & Q(company__isnull=True)),
                name="system_block_one_target",
            ),
            models.UniqueConstraint(
                fields=["company"],
                condition=Q(ended_at__isnull=True, company__isnull=False),
                name="system_block_one_active_company",
            ),
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(ended_at__isnull=True, user__isnull=False),
                name="system_block_one_active_user",
            ),
        ]

    def __str__(self) -> str:
        return f"block {self.id}"


# --- Merge and split of global accounts (§ 24.1.8) ------------------------------------------------


class AccountMerge(models.Model):
    """The merge of a second personal account into the main one by the support. The moved links
    are kept (`AccountMergeItem`) so that a split returns them to their accounts."""

    class State(models.TextChoices):
        MERGED = "merged"
        SPLITTING = "splitting"
        SPLIT = "split"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    main_user = models.ForeignKey(UserAccount, on_delete=models.PROTECT, related_name="+")
    secondary_user = models.ForeignKey(UserAccount, on_delete=models.PROTECT, related_name="+")
    # The basis: why both accounts belong to one person (§ 24.1.8).
    reason = models.TextField()
    ticket = models.ForeignKey(
        SupportTicket, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    state = models.CharField(max_length=10, choices=State.choices, default=State.MERGED)
    merged_at = models.DateTimeField(default=timezone.now)
    merged_by_user_id = models.UUIDField()
    split_reason = models.TextField(blank=True, default="")
    split_started_at = models.DateTimeField(null=True, blank=True)
    split_started_by_user_id = models.UUIDField(null=True, blank=True)
    split_at = models.DateTimeField(null=True, blank=True)
    split_by_user_id = models.UUIDField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "account_merge"
        constraints = [
            models.CheckConstraint(
                condition=~Q(main_user=models.F("secondary_user")), name="account_merge_two_users"
            ),
            models.UniqueConstraint(
                fields=["secondary_user"],
                condition=~Q(state="split"),
                name="account_merge_one_open_per_secondary",
            ),
        ]

    def __str__(self) -> str:
        return f"merge {self.secondary_user_id}->{self.main_user_id}"


class AccountMergeItem(models.Model):
    """A link at the moment of the merge: moved from the second account, or already of the main
    one. It is the history the split restores from (§ 24.1.8)."""

    class Phase(models.TextChoices):
        MOVED = "moved"
        KEPT = "kept"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    merge = models.ForeignKey(AccountMerge, on_delete=models.PROTECT, related_name="items")
    phase = models.CharField(max_length=10, choices=Phase.choices)
    module = models.CharField(max_length=30)
    kind = models.CharField(max_length=30)
    record_id = models.UUIDField()
    label = models.JSONField(default=dict, encoder=DjangoJSONEncoder)

    class Meta:
        db_table = "account_merge_item"
        constraints = [
            models.UniqueConstraint(
                fields=["merge", "module", "kind", "record_id"], name="account_merge_item_uniq"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.phase} {self.module}.{self.kind}:{self.record_id}"


class AccountSplitAssignment(models.Model):
    """The decision of the support on a link that appeared after the merge: which account it
    belongs to (§ 24.1.8). A new decision on the same link replaces the earlier one only until the
    split is completed; the earlier one stays."""

    class Target(models.TextChoices):
        MAIN = "main"
        SECONDARY = "secondary"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    merge = models.ForeignKey(AccountMerge, on_delete=models.PROTECT, related_name="assignments")
    module = models.CharField(max_length=30)
    kind = models.CharField(max_length=30)
    record_id = models.UUIDField()
    label = models.JSONField(default=dict, encoder=DjangoJSONEncoder)
    target = models.CharField(max_length=10, choices=Target.choices)
    decided_by_user_id = models.UUIDField()
    decided_at = models.DateTimeField(default=timezone.now)
    replaced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "account_split_assignment"
        constraints = [
            models.UniqueConstraint(
                fields=["merge", "module", "kind", "record_id"],
                condition=Q(replaced_at__isnull=True),
                name="account_split_assignment_current_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.module}.{self.kind}:{self.record_id}->{self.target}"


# --- Recovery of access through the support (§ 2.3.1; decision В-11.2) ---------------------------


class RecoveryCase(models.Model):
    """A request to recover access through the support. The applicant names the account by its
    c4rsys ID or username and a contact he knows; a new email is confirmed by a code and attached
    only after the check. Two authorized staff members confirm, the initiator of the decision
    does not; the recovery takes effect 24 hours after the second confirmation, at once only for
    an urgent recovery with its own basis."""

    class State(models.TextChoices):
        NEW = "new"
        IN_REVIEW = "in_review"
        INFO_REQUESTED = "info_requested"
        PROPOSED = "proposed"  # a decision to recover waits for the two confirmations
        APPROVED = "approved"  # confirmed; waits for the 24 hours
        COMPLETED = "completed"
        REFUSED = "refused"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    number = models.BigIntegerField(unique=True)
    # As the applicant wrote them; the account is found by the support.
    identifier = models.CharField(max_length=100)
    known_contact = models.CharField(max_length=254)
    new_email = models.CharField(max_length=254, blank=True, default="")
    description = models.TextField(blank=True, default="")
    user = models.ForeignKey(
        UserAccount, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    ticket = models.ForeignKey(
        SupportTicket, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    state = models.CharField(max_length=20, choices=State.choices, default=State.NEW)
    created_at = models.DateTimeField(default=timezone.now)
    ip = models.GenericIPAddressField(null=True, blank=True)
    # The decision to recover: its initiator, the assessment, the unavailable contacts.
    proposed_by = models.ForeignKey(
        SystemStaffMember, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    proposed_at = models.DateTimeField(null=True, blank=True)
    assessment = models.TextField(blank=True, default="")
    unavailable_contact_ids = ArrayField(models.UUIDField(), default=list, blank=True)
    urgent = models.BooleanField(default=False)
    urgent_basis = models.TextField(blank=True, default="")
    approved_at = models.DateTimeField(null=True, blank=True)
    effective_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    refused_at = models.DateTimeField(null=True, blank=True)
    refused_by_user_id = models.UUIDField(null=True, blank=True)
    # Told to the applicant; no closed data of the account (§ 2.3.1).
    refusal_reason = models.TextField(blank=True, default="")
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "recovery_case"
        constraints = [
            models.CheckConstraint(
                condition=Q(urgent=False) | ~Q(urgent_basis=""), name="recovery_case_urgent_basis"
            ),
        ]

    def __str__(self) -> str:
        return f"recovery {self.number}"


class RecoveryApproval(models.Model):
    """A confirmation of the decision to recover by an authorized staff member (§ 2.3.1)."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    case = models.ForeignKey(RecoveryCase, on_delete=models.PROTECT, related_name="approvals")
    member = models.ForeignKey(SystemStaffMember, on_delete=models.PROTECT, related_name="+")
    comment = models.TextField(blank=True, default="")
    approved_at = models.DateTimeField(default=timezone.now)
    # A new decision (after a change of the proposal) makes earlier confirmations void.
    void_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "recovery_approval"
        constraints = [
            models.UniqueConstraint(
                fields=["case", "member"],
                condition=Q(void_at__isnull=True),
                name="recovery_approval_once_per_member",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.case_id}:{self.member_id}"
