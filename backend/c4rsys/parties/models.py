"""Local client cards of a company (ТЗ § 6.1, § 6.2).

A card is a company's own record, not a c4rsys profile (§ 6.2.4); it works without an account
(§ 6.2.10). Linking to a profile (§ 6.2.5–6.2.6) comes later; `linked_user_id` is kept for it.
"""

from django.db import models
from django.db.models import Q
from django.utils.timezone import now

from c4rsys.companies.models import Company
from c4rsys.platform.ids import uuid7


class ClientCard(models.Model):
    """A person or a company (§ 6.1.1). The client base is shared by the whole company, not by
    location (§ 6.1.9)."""

    class Kind(models.TextChoices):
        PERSON = "person"
        COMPANY = "company"

    class State(models.TextChoices):
        ACTIVE = "active"
        ARCHIVED = "archived"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    first_name = models.CharField(max_length=150, blank=True, default="")
    last_name = models.CharField(max_length=150, blank=True, default="")
    middle_name = models.CharField(max_length=150, blank=True, default="")
    birth_date = models.DateField(null=True, blank=True)
    company_name = models.CharField(max_length=200, blank=True, default="")
    linked_user_id = models.UUIDField(null=True, blank=True)
    state = models.CharField(max_length=10, choices=State.choices, default=State.ACTIVE)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "client_card"
        constraints = [
            models.UniqueConstraint(fields=["company", "id"], name="client_card_company_id_uniq"),
            # Minimum of § 6.2.7–6.2.8; the contact minimum is checked by the commands.
            models.CheckConstraint(
                condition=(
                    Q(kind="person") & ~Q(first_name="") & ~Q(last_name="") & Q(company_name="")
                )
                | (
                    Q(kind="company")
                    & ~Q(company_name="")
                    & Q(first_name="")
                    & Q(last_name="")
                    & Q(middle_name="")
                    & Q(birth_date__isnull=True)
                ),
                name="client_card_minimum_by_kind",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "last_name"], name="client_card_last_name_idx"),
        ]

    def __str__(self) -> str:
        if self.kind == self.Kind.COMPANY:
            return self.company_name
        return f"{self.last_name} {self.first_name}"


class ClientContact(models.Model):
    """Phone or email of a client (decision 22.10.2026). `value_normalized` serves the search
    for matches (§ 6.1.11). `client` is bound to the same company by a composite foreign key."""

    class Kind(models.TextChoices):
        PHONE = "phone"
        EMAIL = "email"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    client = models.ForeignKey(ClientCard, on_delete=models.PROTECT, related_name="contacts")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    value = models.CharField(max_length=254)
    value_normalized = models.CharField(max_length=254)

    class Meta:
        db_table = "client_contact"
        constraints = [
            models.UniqueConstraint(
                fields=["client", "kind", "value_normalized"], name="client_contact_uniq"
            ),
        ]
        indexes = [
            models.Index(
                fields=["company", "kind", "value_normalized"], name="client_contact_search_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind}:{self.value}"
