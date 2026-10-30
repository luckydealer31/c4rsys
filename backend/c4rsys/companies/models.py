"""Company and the user's relations with it (ТЗ гл. 3, § 5.1).

Ownership of a share and work in the company are separate relations (§ 3.3.3): a share gives only
owner rights (§ 3.3.4), working authority comes from a position through employment.
"""

from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.db.models import Q
from django.utils.timezone import now

from c4rsys.accounts.models import UserAccount
from c4rsys.files.models import StoredFile
from c4rsys.platform.ids import uuid7
from c4rsys.platform.models import Currency


class Company(models.Model):
    """Profile of § 3.1.1, § 3.1.6, § 22.1.4, § 22.1.7.

    The time zone is empty until the user confirms one; main currency until it is chosen.
    The other fields of § 3.1.5 are optional. `setup_completed_at` is
    set by «Complete» of the setup wizard while at least one context is ready; from then the
    interface shows «Orders» (customer's decision of 24.10.2026).
    """

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    public_id = models.CharField(max_length=40, unique=True)
    display_name = models.CharField(max_length=200)
    logo = models.ForeignKey(
        StoredFile, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    timezone = models.CharField(max_length=64, blank=True, default="")
    main_currency = models.ForeignKey(
        Currency, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    setup_completed_at = models.DateTimeField(null=True, blank=True)
    # § 3.6.1: a voluntarily deactivated company keeps its data and ID; new activity is refused
    # until it is restored (companies.deactivation).
    deactivated_at = models.DateTimeField(null=True, blank=True)
    # Explicit answers of the setup wizard (§ 22.1.10–22.1.11): «work without employees»,
    # «no required documents». A choice of the wizard only, it changes no rule.
    setup_choices = models.JSONField(default=dict, blank=True)
    # Optional profile data of § 3.1.5; filled after creation (§ 3.1.2).
    legal_name = models.CharField(max_length=300, blank=True, default="")
    country = models.CharField(max_length=2, blank=True, default="")  # ISO 3166-1 alpha-2
    legal_form = models.CharField(max_length=100, blank=True, default="")
    identifiers = models.JSONField(default=list, blank=True)  # [{"type": …, "value": …}]
    legal_address = models.CharField(max_length=500, blank=True, default="")
    actual_address = models.CharField(max_length=500, blank=True, default="")
    phone = models.CharField(max_length=40, blank=True, default="")
    email = models.CharField(max_length=254, blank=True, default="")
    website = models.CharField(max_length=300, blank=True, default="")
    description = models.TextField(blank=True, default="")
    # § 3.1.8: additional profile fields the company made public; they are shown in the minimal
    # search card next to the name, logo, country and company ID (§ 3.5.3).
    public_fields = ArrayField(models.CharField(max_length=40), default=list, blank=True)
    created_by = models.ForeignKey(UserAccount, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(default=now)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "company"
        constraints = [
            models.CheckConstraint(
                condition=~Q(display_name=""), name="company_display_name_not_empty"
            ),
        ]

    def __str__(self) -> str:
        return self.public_id


class OwnershipShare(models.Model):
    """Share of a user or of another company in the company (§ 3.3.4).

    A row is never changed in its percent: a change closes the row (`valid_to`) and opens a new
    one, so the rows are the history of ownership (§ 3.4.3). The current rows sum to 100%.
    """

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="shares")
    owner_user = models.ForeignKey(
        UserAccount, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    owner_company = models.ForeignKey(
        Company, on_delete=models.PROTECT, null=True, blank=True, related_name="held_shares"
    )
    percent = models.DecimalField(max_digits=7, decimal_places=4)
    valid_from = models.DateTimeField(default=now)
    valid_to = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "ownership_share"
        constraints = [
            models.CheckConstraint(
                condition=Q(percent__gt=0) & Q(percent__lte=100),
                name="ownership_share_percent_range",
            ),
            models.CheckConstraint(
                condition=Q(owner_user__isnull=False, owner_company__isnull=True)
                | Q(owner_user__isnull=True, owner_company__isnull=False),
                name="ownership_share_one_owner",
            ),
            # § 3.5.4: a company does not own its own share directly.
            models.CheckConstraint(
                condition=~Q(owner_company=models.F("company")),
                name="ownership_share_not_own_company",
            ),
            models.UniqueConstraint(
                fields=["company", "owner_user"],
                condition=Q(valid_to__isnull=True, owner_user__isnull=False),
                name="ownership_share_current_user_uniq",
            ),
            models.UniqueConstraint(
                fields=["company", "owner_company"],
                condition=Q(valid_to__isnull=True, owner_company__isnull=False),
                name="ownership_share_current_company_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.owner_user_id or self.owner_company_id} {self.percent}%"


class Position(models.Model):
    """Entry of the company's own handbook of positions (§ 5.1.5).

    A position created by the system has a `code` and an empty `name`; the interface shows the
    translated name of the code until the company renames it.
    """

    class Code(models.TextChoices):
        DIRECTOR = "director"
        # Dedicated positions of owners (§ 3.3.4, § 3.4.1): of a user owner for himself, of an
        # owner company for its representative.
        OWNER = "owner"
        OWNER_REPRESENTATIVE = "owner_representative"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="positions")
    code = models.CharField(max_length=40, choices=Code.choices, blank=True, default="")
    name = models.CharField(max_length=150, blank=True, default="")
    is_top_authority = models.BooleanField(default=False)
    is_owner_position = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "position"
        constraints = [
            models.UniqueConstraint(fields=["company", "id"], name="position_company_id_uniq"),
            models.CheckConstraint(
                condition=~Q(code="") | ~Q(name=""), name="position_has_code_or_name"
            ),
        ]

    def __str__(self) -> str:
        return self.name or self.code


class Employment(models.Model):
    """Employee card and its employment relation with a user (§ 3.1.4, § 5.1.1).

    `position` is additionally bound to the same company by a composite foreign key
    (company_id, position_id) created in migration 0002.
    """

    class State(models.TextChoices):
        INVITED = "invited"
        ACTIVE = "active"
        BLOCKED = "blocked"
        TERMINATED = "terminated"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="employments")
    user = models.ForeignKey(
        UserAccount, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    position = models.ForeignKey(Position, on_delete=models.PROTECT, related_name="+")
    state = models.CharField(max_length=20, choices=State.choices)
    started_at = models.DateTimeField(null=True, blank=True)
    work_email = models.CharField(max_length=254, blank=True, default="")
    work_phone = models.CharField(max_length=40, blank=True, default="")
    # Data the user chose to give the company on acceptance; first and last name always (§ 5.1.7).
    shares_middle_name = models.BooleanField(default=False)
    shares_email = models.BooleanField(default=False)
    shares_photo = models.BooleanField(default=False)
    shares_phone = models.BooleanField(default=False)
    # Termination of access (§ 5.3.1): the account, the history and the operations stay.
    terminated_at = models.DateTimeField(null=True, blank=True)
    terminated_by_user_id = models.UUIDField(null=True, blank=True)
    termination_reason = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(default=now)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "employment"
        constraints = [
            models.UniqueConstraint(fields=["company", "id"], name="employment_company_id_uniq"),
            models.UniqueConstraint(
                fields=["company", "user"],
                condition=Q(user__isnull=False) & ~Q(state="terminated"),
                name="employment_current_user_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"employment {self.id}"


class Department(models.Model):
    """A department of the organisation structure (§ 5.2.1).

    A department is subordinate either to another department or to a manager (an employee);
    exactly one of the two. Self-subordination and cycles are refused by companies.orgstructure.
    An archived department keeps its history and takes no new links.
    """

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    name = models.CharField(max_length=150)
    parent_department = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    parent_employment = models.ForeignKey(
        Employment, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(default=now)
    archived_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "department"
        constraints = [
            models.UniqueConstraint(fields=["company", "id"], name="department_company_id_uniq"),
            models.CheckConstraint(condition=~Q(name=""), name="department_name_not_empty"),
            models.CheckConstraint(
                condition=Q(parent_department__isnull=False, parent_employment__isnull=True)
                | Q(parent_department__isnull=True, parent_employment__isnull=False),
                name="department_one_parent",
            ),
            models.CheckConstraint(
                condition=~Q(parent_department=models.F("id")), name="department_not_own_parent"
            ),
        ]

    def __str__(self) -> str:
        return self.name


class DepartmentMember(models.Model):
    """An employee of a department (§ 5.2.1, § 5.2.3). Rows are not deleted: leaving the
    department sets `removed_at`, so the history stays."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="+")
    employment = models.ForeignKey(Employment, on_delete=models.PROTECT, related_name="+")
    added_at = models.DateTimeField(default=now)
    added_by_user_id = models.UUIDField(null=True, blank=True)
    removed_at = models.DateTimeField(null=True, blank=True)
    removed_by_user_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "department_member"
        constraints = [
            models.UniqueConstraint(
                fields=["department", "employment"],
                condition=Q(removed_at__isnull=True),
                name="department_member_current_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.employment_id}@{self.department_id}"


class Responsibility(models.Model):
    """A responsible of an employee: another employee or a department (§ 5.2.1, § 5.2.4).

    An employee may have several. A link is ended, not deleted (`ended_at`), so the history of
    subordination stays. The position in the structure gives no rights (§ 5.2.1).
    """

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    subordinate = models.ForeignKey(Employment, on_delete=models.PROTECT, related_name="+")
    responsible_employment = models.ForeignKey(
        Employment, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    responsible_department = models.ForeignKey(
        Department, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(default=now)
    created_by_user_id = models.UUIDField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    ended_by_user_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "responsibility"
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    responsible_employment__isnull=False, responsible_department__isnull=True
                )
                | Q(responsible_employment__isnull=True, responsible_department__isnull=False),
                name="responsibility_one_responsible",
            ),
            # § 5.2.4: no self-subordination.
            models.CheckConstraint(
                condition=~Q(responsible_employment=models.F("subordinate")),
                name="responsibility_not_self",
            ),
            models.UniqueConstraint(
                fields=["subordinate", "responsible_employment"],
                condition=Q(ended_at__isnull=True, responsible_employment__isnull=False),
                name="responsibility_current_employee_uniq",
            ),
            models.UniqueConstraint(
                fields=["subordinate", "responsible_department"],
                condition=Q(ended_at__isnull=True, responsible_department__isnull=False),
                name="responsibility_current_department_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "ended_at"], name="responsibility_company_idx"),
        ]

    def __str__(self) -> str:
        target = self.responsible_employment_id or self.responsible_department_id
        return f"{self.subordinate_id} <- {target}"


WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def schedule_24_7() -> dict[str, list[dict[str, str]]]:
    """Weekly schedule open around the clock, the default of a new location (§ 22.1.6)."""
    return {day: [{"from": "00:00", "to": "24:00"}] for day in WEEKDAYS}


class Location(models.Model):
    """Place of accounting of the company (§ 4.1.2, § 4.1.4).

    The state «draft / active» is not stored: it follows from the time zone and the readiness of
    contexts (§ 4.4.3–4.4.4), see locations.location_state. A deactivated location (§ 4.4.1)
    keeps its history and accepts no new operations until it is restored.
    """

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="locations")
    name = models.CharField(max_length=200)
    timezone = models.CharField(max_length=64, blank=True, default="")
    address = models.CharField(max_length=500, blank=True, default="")
    deactivated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=now)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "location"
        constraints = [
            models.UniqueConstraint(fields=["company", "id"], name="location_company_id_uniq"),
            models.CheckConstraint(condition=~Q(name=""), name="location_name_not_empty"),
        ]

    def __str__(self) -> str:
        return self.name


class ActivityContext(models.Model):
    """Direction of work in a location (§ 4.1.6–4.1.8). At most one of each type (§ 4.1.7).

    Decision of 25.10.2026: only the warehouse context stores, so a location has at most one
    warehouse; rental and service have no storage of their own and take from the warehouse.

    `location` is additionally bound to the same company by a composite foreign key (migration).
    Without its own schedule the context inherits the schedule of the location (§ 4.2.3).
    """

    class Type(models.TextChoices):
        RENTAL = "rental"
        SERVICE = "service"
        WAREHOUSE = "warehouse"

    class State(models.TextChoices):
        ENABLED = "enabled"
        DISABLED = "disabled"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="contexts")
    type = models.CharField(max_length=20, choices=Type.choices)
    state = models.CharField(max_length=20, choices=State.choices, default=State.ENABLED)
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "activity_context"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "id"], name="activity_context_company_id_uniq"
            ),
            models.UniqueConstraint(fields=["location", "type"], name="activity_context_type_uniq"),
        ]

    def __str__(self) -> str:
        return f"{self.type}@{self.location_id}"


class LocationSchedule(models.Model):
    """Weekly schedule and date exceptions of a location (§ 4.2.3, § 22.1.6)."""

    location = models.OneToOneField(
        Location, on_delete=models.PROTECT, primary_key=True, related_name="schedule"
    )
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    weekly = models.JSONField(default=schedule_24_7)
    exceptions = models.JSONField(default=list)

    class Meta:
        db_table = "location_schedule"

    def __str__(self) -> str:
        return f"schedule {self.location_id}"


class Permission(models.Model):
    """System catalog of permissions, seeded from companies.permissions."""

    code = models.CharField(max_length=60, primary_key=True)
    section = models.CharField(max_length=30)

    class Meta:
        db_table = "permission"

    def __str__(self) -> str:
        return self.code


class PermissionSet(models.Model):
    """Ready-made set of permissions. System sets have no company (§ 23.1.5, § 23.1.14)."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    code = models.CharField(max_length=40, blank=True, default="")
    name = models.CharField(max_length=150, blank=True, default="")
    is_system = models.BooleanField(default=False)
    permissions = models.ManyToManyField(Permission, through="PermissionSetItem", related_name="+")

    class Meta:
        db_table = "permission_set"
        constraints = [
            models.UniqueConstraint(
                fields=["code"],
                condition=Q(is_system=True),
                name="permission_set_system_code_uniq",
            ),
            models.CheckConstraint(
                condition=Q(is_system=False) | Q(company__isnull=True),
                name="permission_set_system_has_no_company",
            ),
        ]

    def __str__(self) -> str:
        return self.code or self.name


class PermissionSetItem(models.Model):
    permission_set = models.ForeignKey(PermissionSet, on_delete=models.CASCADE, related_name="+")
    permission = models.ForeignKey(Permission, on_delete=models.PROTECT, related_name="+")

    class Meta:
        db_table = "permission_set_item"
        constraints = [
            models.UniqueConstraint(
                fields=["permission_set", "permission"], name="permission_set_item_uniq"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.permission_set_id}:{self.permission_id}"


class PermissionAssignment(models.Model):
    """One grant of access to an employee with its own scope (§ 23.1.1, § 23.1.13).

    * `set` — a permission set; `permissions` is the copy of its codes at the moment of
      assignment (§ 23.1.3).
    * `grant` / `deny` — personal addition or restriction of single permissions (§ 23.1.2).

    Assignments are not deleted: a revoked one keeps who granted and who revoked it (§ 23.3.3).
    """

    class Kind(models.TextChoices):
        SET = "set"
        GRANT = "grant"
        DENY = "deny"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    employment = models.ForeignKey(Employment, on_delete=models.PROTECT, related_name="assignments")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    permission_set = models.ForeignKey(
        PermissionSet, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    permissions = ArrayField(models.CharField(max_length=60))
    all_locations = models.BooleanField(default=True)
    all_contexts = models.BooleanField(default=True)
    # «All in the accessible scope» or a list of cash desks and accounts (§ 23.1.11–23.1.12).
    all_cash_accounts = models.BooleanField(default=True)
    locations = models.ManyToManyField(Location, through="AssignmentLocation", related_name="+")
    contexts = models.ManyToManyField(
        ActivityContext, through="AssignmentContext", related_name="+"
    )
    # Rights a representative got with the dedicated position of an owner company within its limit
    # (§ 3.4.1); they end with the representative and are not edited as ordinary rights.
    owner_seat = models.ForeignKey(
        "OwnerSeat", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    granted_by_user_id = models.UUIDField()
    granted_at = models.DateTimeField(default=now)
    revoked_by_user_id = models.UUIDField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "permission_assignment"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "id"], name="permission_assignment_company_id_uniq"
            ),
            models.CheckConstraint(
                condition=Q(kind="set", permission_set__isnull=False)
                | (~Q(kind="set") & Q(permission_set__isnull=True)),
                name="permission_assignment_set_matches_kind",
            ),
        ]
        indexes = [
            models.Index(fields=["employment", "revoked_at"], name="perm_assignment_active_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.kind} {self.id}"


class AssignmentLocation(models.Model):
    """Selected location of an assignment whose scope is not the whole company (§ 23.1.9)."""

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    assignment = models.ForeignKey(PermissionAssignment, on_delete=models.PROTECT, related_name="+")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="+")

    class Meta:
        db_table = "permission_assignment_location"
        constraints = [
            models.UniqueConstraint(
                fields=["assignment", "location"], name="permission_assignment_location_uniq"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.assignment_id}@{self.location_id}"


class AssignmentContext(models.Model):
    """Selected context of an assignment with a concrete list of contexts (§ 23.1.10)."""

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    assignment = models.ForeignKey(PermissionAssignment, on_delete=models.PROTECT, related_name="+")
    context = models.ForeignKey(ActivityContext, on_delete=models.PROTECT, related_name="+")

    class Meta:
        db_table = "permission_assignment_context"
        constraints = [
            models.UniqueConstraint(
                fields=["assignment", "context"], name="permission_assignment_context_uniq"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.assignment_id}@{self.context_id}"


class AssignmentCashAccount(models.Model):
    """Selected cash desk or account of an assignment with a concrete list (§ 23.1.11)."""

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    assignment = models.ForeignKey(
        PermissionAssignment, on_delete=models.PROTECT, related_name="cash_accounts"
    )
    cash_account = models.ForeignKey(
        "finance.CashAccount", on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        db_table = "permission_assignment_cash_account"
        constraints = [
            models.UniqueConstraint(
                fields=["assignment", "cash_account"],
                name="permission_assignment_cash_account_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.assignment_id}@{self.cash_account_id}"


class Invitation(models.Model):
    """Invitation of an employee (§ 2.5.1–2.5.2, § 5.1.1, § 5.1.3).

    The target is a user found by ID or confirmed email, or an email without a profile yet; a user
    who registers with that email sees the invitation without a new one (§ 5.1.3). `offered` holds
    the offered rights; they become assignments on acceptance.
    """

    class State(models.TextChoices):
        PENDING = "pending"
        ACCEPTED = "accepted"
        DECLINED = "declined"
        CANCELLED = "cancelled"
        EXPIRED = "expired"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    employment = models.ForeignKey(Employment, on_delete=models.PROTECT, related_name="invitations")
    target_user_id = models.UUIDField(null=True, blank=True)
    target_email = models.CharField(max_length=254, blank=True, default="")
    state = models.CharField(max_length=20, choices=State.choices, default=State.PENDING)
    offered = models.JSONField(default=dict)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    expires_at = models.DateTimeField()
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "invitation"
        constraints = [
            models.CheckConstraint(
                condition=Q(target_user_id__isnull=False) | ~Q(target_email=""),
                name="invitation_has_target",
            ),
            models.UniqueConstraint(
                fields=["employment"],
                condition=Q(state="pending"),
                name="invitation_one_pending_per_card",
            ),
        ]
        indexes = [
            models.Index(fields=["target_user_id", "state"], name="invitation_target_user_idx"),
            models.Index(fields=["target_email", "state"], name="invitation_target_email_idx"),
        ]

    def __str__(self) -> str:
        return f"invitation {self.id}"


# --- Ownership (ТЗ § 3.3–3.6) ------------------------------------------------------------------


def _one(prefix: str, name: str) -> models.CheckConstraint:
    """Exactly one of `<prefix>_user` and `<prefix>_company` is set."""
    user, company = f"{prefix}_user__isnull", f"{prefix}_company__isnull"
    return models.CheckConstraint(
        condition=Q(**{user: False, company: True}) | Q(**{user: True, company: False}),
        name=name,
    )


class OwnerSeat(models.Model):
    """The dedicated position of an owner (§ 3.3.4, § 3.4.1).

    For a user owner the position is for himself: `holder` is his employment in the company. For an
    owner company it is the position of its representative: `rights_limit` is the limit of rights
    set by the top authority of this company; the owner company appoints a person (`holder`) with
    rights within it, or leaves the position vacant. A seat is prepared for the recipient of a
    share transfer, becomes active when the share passes and ends after the owner transferred the
    whole share (§ 3.4.4); the history stays.
    """

    class State(models.TextChoices):
        PREPARED = "prepared"
        ACTIVE = "active"
        ENDED = "ended"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    position = models.ForeignKey(Position, on_delete=models.PROTECT, related_name="+")
    owner_user = models.ForeignKey(
        UserAccount, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    owner_company = models.ForeignKey(
        Company, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    holder = models.ForeignKey(
        Employment, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    # [RightSpec.as_dict()] — the limit for the representative of an owner company (§ 3.4.1).
    rights_limit = models.JSONField(default=list, blank=True)
    state = models.CharField(max_length=10, choices=State.choices, default=State.PREPARED)
    created_at = models.DateTimeField(default=now)
    activated_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "owner_seat"
        constraints = [
            _one("owner", "owner_seat_one_owner"),
            models.UniqueConstraint(
                fields=["company", "owner_user"],
                condition=~Q(state="ended") & Q(owner_user__isnull=False),
                name="owner_seat_current_user_uniq",
            ),
            models.UniqueConstraint(
                fields=["company", "owner_company"],
                condition=~Q(state="ended") & Q(owner_company__isnull=False),
                name="owner_seat_current_company_uniq",
            ),
            models.CheckConstraint(
                condition=Q(state="ended", ended_at__isnull=False)
                | (~Q(state="ended") & Q(ended_at__isnull=True)),
                name="owner_seat_ended_has_time",
            ),
        ]

    def __str__(self) -> str:
        return f"seat {self.id}"


class OwnershipPolicy(models.Model):
    """Approval policies of the company (§ 3.4.2, § 3.6.2).

    * `transfer_mode` — additional approval of a share transfer by the other co-owners; without a
      row «none» (§ 3.4.2).
    * `state_mode` — approval of deactivation and restoration; «unset» means all owners when the
      company has several of them (§ 3.6.2).
    `percent` modes need owners together holding at least the given percent of the capital.
    """

    class Mode(models.TextChoices):
        UNSET = "unset"
        NONE = "none"
        ALL = "all"
        PERCENT = "percent"

    company = models.OneToOneField(
        Company, on_delete=models.PROTECT, primary_key=True, related_name="+"
    )
    transfer_mode = models.CharField(max_length=10, choices=Mode.choices, default=Mode.NONE)
    transfer_percent = models.DecimalField(max_digits=7, decimal_places=4, null=True, blank=True)
    state_mode = models.CharField(max_length=10, choices=Mode.choices, default=Mode.UNSET)
    state_percent = models.DecimalField(max_digits=7, decimal_places=4, null=True, blank=True)
    updated_by_user_id = models.UUIDField(null=True, blank=True)
    updated_at = models.DateTimeField(default=now)
    version = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "ownership_policy"
        constraints = [
            models.CheckConstraint(
                condition=~Q(transfer_mode="unset"), name="ownership_policy_transfer_mode_set"
            ),
            models.CheckConstraint(
                condition=(
                    Q(transfer_mode="percent", transfer_percent__gt=0, transfer_percent__lte=100)
                    | (~Q(transfer_mode="percent") & Q(transfer_percent__isnull=True))
                ),
                name="ownership_policy_transfer_percent",
            ),
            models.CheckConstraint(
                condition=(
                    Q(state_mode="percent", state_percent__gt=0, state_percent__lte=100)
                    | (~Q(state_mode="percent") & Q(state_percent__isnull=True))
                ),
                name="ownership_policy_state_percent",
            ),
        ]

    def __str__(self) -> str:
        return f"policy {self.company_id}"


class ShareTransfer(models.Model):
    """A request to transfer a part of a share (§ 3.4.3, § 3.4.6).

    The share stays with the sender until every needed confirmation; then one operation reduces
    the sender's share and creates or increases the recipient's. `successor` is the chosen
    successor of the top authority when the top authority gives away the whole remaining share.
    """

    class State(models.TextChoices):
        PENDING = "pending"
        COMPLETED = "completed"
        DECLINED = "declined"
        CANCELLED = "cancelled"
        EXPIRED = "expired"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    sender_user = models.ForeignKey(
        UserAccount, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    sender_company = models.ForeignKey(
        Company, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    recipient_user = models.ForeignKey(
        UserAccount, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    recipient_company = models.ForeignKey(
        Company, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    percent = models.DecimalField(max_digits=7, decimal_places=4)
    successor_user = models.ForeignKey(
        UserAccount, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    successor_company = models.ForeignKey(
        Company, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    recipient_seat = models.ForeignKey(
        OwnerSeat, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    # The co-owner approval policy at the moment of the request (§ 3.4.2).
    policy_mode = models.CharField(max_length=10)
    policy_percent = models.DecimalField(max_digits=7, decimal_places=4, null=True, blank=True)
    state = models.CharField(max_length=10, choices=State.choices, default=State.PENDING)
    initiated_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    expires_at = models.DateTimeField()
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "share_transfer"
        constraints = [
            models.CheckConstraint(
                condition=Q(percent__gt=0) & Q(percent__lte=100),
                name="share_transfer_percent_range",
            ),
            _one("sender", "share_transfer_one_sender"),
            _one("recipient", "share_transfer_one_recipient"),
            models.CheckConstraint(
                condition=Q(successor_user__isnull=True) | Q(successor_company__isnull=True),
                name="share_transfer_one_successor",
            ),
        ]
        indexes = [models.Index(fields=["company", "state"], name="share_transfer_state_idx")]

    def __str__(self) -> str:
        return f"transfer {self.id}"


class ShareTransferApproval(models.Model):
    """One confirmation a transfer waits for (§ 3.4.1–3.4.3, § 3.4.6).

    * `sender_owner` — each owner of the owner company disposing of its share;
    * `recipient` — the recipient user accepts;
    * `recipient_owner` — each owner of the recipient company;
    * `coowner` — the other co-owners by the approval policy;
    * `successor` — the chosen successor accepts the top authority.
    A party company decides through its top authority or an employee with full rights (PROPOSAL).
    """

    class Role(models.TextChoices):
        SENDER_OWNER = "sender_owner"
        RECIPIENT = "recipient"
        RECIPIENT_OWNER = "recipient_owner"
        COOWNER = "coowner"
        SUCCESSOR = "successor"

    class State(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        DECLINED = "declined"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    transfer = models.ForeignKey(ShareTransfer, on_delete=models.PROTECT, related_name="approvals")
    role = models.CharField(max_length=20, choices=Role.choices)
    party_user = models.ForeignKey(
        UserAccount, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    party_company = models.ForeignKey(
        Company, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    state = models.CharField(max_length=10, choices=State.choices, default=State.PENDING)
    decided_by_user_id = models.UUIDField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "share_transfer_approval"
        constraints = [_one("party", "share_transfer_approval_one_party")]

    def __str__(self) -> str:
        return f"{self.role} {self.id}"


class CompanyStateRequest(models.Model):
    """Deactivation or restoration of the company waiting for the owners (§ 3.6.1–3.6.2)."""

    class Kind(models.TextChoices):
        DEACTIVATE = "deactivate"
        RESTORE = "restore"

    class State(models.TextChoices):
        PENDING = "pending"
        COMPLETED = "completed"
        DECLINED = "declined"
        CANCELLED = "cancelled"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    state = models.CharField(max_length=10, choices=State.choices, default=State.PENDING)
    initiated_by_user_id = models.UUIDField()
    initiated_by_employment_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(default=now)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "company_state_request"
        constraints = [
            models.UniqueConstraint(
                fields=["company"],
                condition=Q(state="pending"),
                name="company_state_request_one_pending",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind} {self.id}"


class CompanyStateApproval(models.Model):
    """A decision of an owner on a deactivation or restoration request (§ 3.6.2).

    `composition` fingerprints the owners and shares at the moment of the decision: when they
    change, earlier decisions stay in the history but no longer count for the request.
    """

    class Decision(models.TextChoices):
        APPROVED = "approved"
        DECLINED = "declined"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    request = models.ForeignKey(
        CompanyStateRequest, on_delete=models.PROTECT, related_name="approvals"
    )
    party_user = models.ForeignKey(
        UserAccount, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    party_company = models.ForeignKey(
        Company, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    decision = models.CharField(max_length=10, choices=Decision.choices)
    composition = models.CharField(max_length=64)
    decided_by_user_id = models.UUIDField()
    decided_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "company_state_approval"
        constraints = [_one("party", "company_state_approval_one_party")]

    def __str__(self) -> str:
        return f"{self.decision} {self.id}"
