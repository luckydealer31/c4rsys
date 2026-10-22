"""Company and the user's relations with it (ТЗ гл. 3, § 5.1).

Ownership of a share and work in the company are separate relations (§ 3.3.3): a share gives only
owner rights (§ 3.3.4), working authority comes from a position through employment.
"""

from django.db import models
from django.db.models import Q
from django.utils.timezone import now

from c4rsys.accounts.models import UserAccount
from c4rsys.platform.ids import uuid7
from c4rsys.platform.models import Currency


class Company(models.Model):
    """Profile of § 3.1.1, § 3.1.6, § 22.1.4, § 22.1.7.

    The time zone is empty until the user confirms one; main currency until it is chosen.
    The remaining profile fields of § 3.1.5 are added by later tasks.
    """

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    public_id = models.CharField(max_length=40, unique=True)
    display_name = models.CharField(max_length=200)
    timezone = models.CharField(max_length=64, blank=True, default="")
    main_currency = models.ForeignKey(
        Currency, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
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
    """Share of a user in the company (§ 3.3.4). Owner companies come with share transfers."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="shares")
    owner_user = models.ForeignKey(UserAccount, on_delete=models.PROTECT, related_name="+")
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
        ]

    def __str__(self) -> str:
        return f"{self.owner_user_id} {self.percent}%"


class Position(models.Model):
    """Entry of the company's own handbook of positions (§ 5.1.5).

    A position created by the system has a `code` and an empty `name`; the interface shows the
    translated name of the code until the company renames it.
    """

    class Code(models.TextChoices):
        DIRECTOR = "director"

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
    created_at = models.DateTimeField(default=now)

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
