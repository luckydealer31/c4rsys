"""Local client cards and counterparties of a company (ТЗ § 6.1, § 6.2).

A card is a company's own record, not a c4rsys profile (§ 6.2.4); it works without an account
(§ 6.2.10). A card is linked to a profile of a person (`linked_user_id`) or of a company
(`linked_company_id`) only after the recipient accepts a request (§ 6.2.5–6.2.6, `ProfileLink`).
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
    # Optional data of the import template (§ 22.3.2).
    address = models.CharField(max_length=500, blank=True, default="")
    comment = models.TextField(blank=True, default="")
    external_id = models.CharField(max_length=100, blank=True, default="")
    # Created by an import as a possible duplicate of another card (§ 22.2.3, § 22.3.2).
    possible_duplicate = models.BooleanField(default=False)
    linked_user_id = models.UUIDField(null=True, blank=True)
    linked_company_id = models.UUIDField(null=True, blank=True)
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
    # The way of contact by this phone: empty — a call or SMS (the default); otherwise a
    # messenger bound to the number (customer's decision of 30.10.2026). Emails have none.
    messenger = models.CharField(max_length=20, blank=True, default="")

    class Meta:
        db_table = "client_contact"
        constraints = [
            models.UniqueConstraint(
                fields=["client", "kind", "value_normalized"], name="client_contact_uniq"
            ),
            models.CheckConstraint(
                condition=Q(messenger="") | Q(kind="phone"), name="client_contact_messenger_phone"
            ),
        ]
        indexes = [
            models.Index(
                fields=["company", "kind", "value_normalized"], name="client_contact_search_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind}:{self.value}"


class Counterparty(models.Model):
    """A supplier of goods or services to the company (§ 6.1.4–6.1.5): a person or a company. It is
    a list of its own, apart from the clients (§ 6.1.7), shared by the whole company (§ 6.1.9).
    Minimal list of the warehouse stage (customer's decision of 25.10.2026): linking to a profile,
    requests are `ProfileLink`; merging comes later."""

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
    company_name = models.CharField(max_length=200, blank=True, default="")
    comment = models.TextField(blank=True, default="")
    # Optional data of the import template (§ 22.3.3).
    registration = models.CharField(max_length=500, blank=True, default="")
    address = models.CharField(max_length=500, blank=True, default="")
    category = models.CharField(max_length=100, blank=True, default="")
    external_id = models.CharField(max_length=100, blank=True, default="")
    possible_duplicate = models.BooleanField(default=False)
    linked_user_id = models.UUIDField(null=True, blank=True)
    linked_company_id = models.UUIDField(null=True, blank=True)
    state = models.CharField(max_length=10, choices=State.choices, default=State.ACTIVE)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "counterparty"
        constraints = [
            models.UniqueConstraint(fields=["company", "id"], name="counterparty_company_id_uniq"),
            # Minimum of § 6.2.7–6.2.8 and § 22.3.3; the contact minimum is checked by the commands.
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
                ),
                name="counterparty_minimum_by_kind",
            ),
        ]

    def __str__(self) -> str:
        if self.kind == self.Kind.COMPANY:
            return self.company_name
        return f"{self.last_name} {self.first_name}"


class CounterpartyContact(models.Model):
    """Phone or email of a counterparty. `counterparty` is bound to the same company by a
    composite foreign key (migration)."""

    class Kind(models.TextChoices):
        PHONE = "phone"
        EMAIL = "email"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    counterparty = models.ForeignKey(
        Counterparty, on_delete=models.PROTECT, related_name="contacts"
    )
    kind = models.CharField(max_length=10, choices=Kind.choices)
    value = models.CharField(max_length=254)
    value_normalized = models.CharField(max_length=254)
    # The way of contact by this phone: empty — a call or SMS (the default); otherwise a
    # messenger bound to the number (customer's decision of 30.10.2026). Emails have none.
    messenger = models.CharField(max_length=20, blank=True, default="")

    class Meta:
        db_table = "counterparty_contact"
        constraints = [
            models.UniqueConstraint(
                fields=["counterparty", "kind", "value_normalized"],
                name="counterparty_contact_uniq",
            ),
            models.CheckConstraint(
                condition=Q(messenger="") | Q(kind="phone"),
                name="counterparty_contact_messenger_phone",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind}:{self.value}"


class ProfileLink(models.Model):
    """A request to link a card to a c4rsys profile and the link it becomes (§ 6.2.5–6.3, § 6.6).

    The target is what the employee entered: a USR or CMP ID, an email or a phone. It only
    addresses the request (§ 6.3.1); an email or phone without a profile is an invitation to
    register tied to this card (§ 6.2.6). The answer is given by the person, or for a company by
    its top-authority director or an employee with the right (§ 6.3.8). `shared` is the snapshot of
    the data the recipient gave (§ 6.4.1). A broken link keeps its history (§ 6.6.3, § 6.6.6).
    """

    class Profile(models.TextChoices):
        PERSON = "person"
        COMPANY = "company"

    class State(models.TextChoices):
        PENDING = "pending"
        ACCEPTED = "accepted"
        DECLINED = "declined"
        CANCELLED = "cancelled"
        EXPIRED = "expired"
        BROKEN = "broken"

    class Initiator(models.TextChoices):
        COMPANY = "company"
        CLIENT = "client"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    client = models.ForeignKey(
        ClientCard, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    counterparty = models.ForeignKey(
        Counterparty, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    profile = models.CharField(max_length=10, choices=Profile.choices)
    target_label = models.CharField(max_length=254)
    target_user_id = models.UUIDField(null=True, blank=True)
    target_company_id = models.UUIDField(null=True, blank=True)
    target_email = models.CharField(max_length=254, blank=True, default="")
    target_phone = models.CharField(max_length=40, blank=True, default="")
    state = models.CharField(max_length=10, choices=State.choices, default=State.PENDING)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    expires_at = models.DateTimeField()
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by_user_id = models.UUIDField(null=True, blank=True)
    shared = models.JSONField(default=dict, blank=True)
    broken_at = models.DateTimeField(null=True, blank=True)
    broken_by_user_id = models.UUIDField(null=True, blank=True)
    broken_initiator = models.CharField(
        max_length=10, choices=Initiator.choices, blank=True, default=""
    )

    class Meta:
        db_table = "profile_link"
        constraints = [
            models.CheckConstraint(
                condition=(Q(client__isnull=False) & Q(counterparty__isnull=True))
                | (Q(client__isnull=True) & Q(counterparty__isnull=False)),
                name="profile_link_one_card",
            ),
            models.CheckConstraint(
                condition=Q(target_user_id__isnull=False)
                | Q(target_company_id__isnull=False)
                | ~Q(target_email="")
                | ~Q(target_phone=""),
                name="profile_link_has_target",
            ),
            models.UniqueConstraint(
                fields=["client"],
                condition=Q(state__in=["pending", "accepted"]),
                name="profile_link_one_open_per_client",
            ),
            models.UniqueConstraint(
                fields=["counterparty"],
                condition=Q(state__in=["pending", "accepted"]),
                name="profile_link_one_open_per_counterparty",
            ),
        ]
        indexes = [
            models.Index(fields=["target_user_id", "state"], name="profile_link_user_idx"),
            models.Index(fields=["target_company_id", "state"], name="profile_link_company_idx"),
            models.Index(fields=["target_email", "state"], name="profile_link_email_idx"),
            models.Index(fields=["target_phone", "state"], name="profile_link_phone_idx"),
        ]

    def __str__(self) -> str:
        return f"profile link {self.id}"
