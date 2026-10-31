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


class ExtendedFields(models.Model):
    """The extended data of a card (§ 6.2.12–6.2.13): none of them is required at creation; a
    document checks the data it needs. Person: identity document, driving licence with the
    allowed categories, driving experience, other documents, addresses of registration and of
    residence. Company: country of registration, legal form, registration and tax numbers, legal
    and actual addresses, head, contact person, bank details, the signing representative and the
    basis of their authority. The shapes of the documents are a PROPOSAL."""

    # {"type", "number", "country", "issued_on", "expires_on"}
    id_document = models.JSONField(null=True, blank=True)
    # {"number", "country", "issued_on", "expires_on", "categories": [...]}
    driver_license = models.JSONField(null=True, blank=True)
    # Driving experience counted from this date (PROPOSAL).
    driving_since = models.DateField(null=True, blank=True)
    # [{"title", "number", "issued_on", "expires_on", "note"}]
    other_documents = models.JSONField(default=list, blank=True)
    registration_address = models.CharField(max_length=500, blank=True, default="")
    residence_address = models.CharField(max_length=500, blank=True, default="")
    country = models.CharField(max_length=2, blank=True, default="")  # ISO 3166-1 alpha-2
    legal_form = models.CharField(max_length=100, blank=True, default="")
    registration_number = models.CharField(max_length=100, blank=True, default="")
    tax_number = models.CharField(max_length=100, blank=True, default="")
    legal_address = models.CharField(max_length=500, blank=True, default="")
    actual_address = models.CharField(max_length=500, blank=True, default="")
    director_name = models.CharField(max_length=200, blank=True, default="")
    contact_person = models.CharField(max_length=200, blank=True, default="")
    bank_details = models.TextField(blank=True, default="")
    signatory_name = models.CharField(max_length=200, blank=True, default="")
    signatory_basis = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        abstract = True


class ClientCard(ExtendedFields):
    """A person or a company (§ 6.1.1). The client base is shared by the whole company, not by
    location (§ 6.1.9)."""

    class Kind(models.TextChoices):
        PERSON = "person"
        COMPANY = "company"

    class State(models.TextChoices):
        ACTIVE = "active"
        ARCHIVED = "archived"
        # Merged into another card of the company (§ 6.2.3, § 6.5.2): closed for new operations.
        MERGED = "merged"

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
    # § 6.2.9–6.2.10: created with only an ID or a contact and a request to the profile; not
    # ready for operations until it holds the minimum of § 6.2.7–6.2.8.
    provisional = models.BooleanField(default=False)
    merged_into = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    merged_at = models.DateTimeField(null=True, blank=True)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "client_card"
        constraints = [
            models.UniqueConstraint(fields=["company", "id"], name="client_card_company_id_uniq"),
            # Minimum of § 6.2.7–6.2.8, except a provisional card (§ 6.2.9); the contact minimum is
            # checked by the commands.
            models.CheckConstraint(
                condition=(
                    Q(kind="person")
                    & Q(company_name="")
                    & (Q(provisional=True) | (~Q(first_name="") & ~Q(last_name="")))
                )
                | (
                    Q(kind="company")
                    & Q(first_name="")
                    & Q(last_name="")
                    & Q(middle_name="")
                    & Q(birth_date__isnull=True)
                    & (Q(provisional=True) | ~Q(company_name=""))
                ),
                name="client_card_minimum_by_kind_v2",
            ),
            models.CheckConstraint(
                condition=(Q(state="merged") & Q(merged_into__isnull=False))
                | (~Q(state="merged") & Q(merged_into__isnull=True)),
                name="client_card_merged_has_main",
            ),
            # One card of the list per profile in a company (§ 6.2.4, § 6.6.6).
            models.UniqueConstraint(
                fields=["company", "linked_user_id"],
                condition=Q(linked_user_id__isnull=False),
                name="client_card_one_per_user",
            ),
            models.UniqueConstraint(
                fields=["company", "linked_company_id"],
                condition=Q(linked_company_id__isnull=False),
                name="client_card_one_per_company",
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


class Counterparty(ExtendedFields):
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
        # Merged into another card of the company (§ 6.2.3, § 6.5.2): closed for new operations.
        MERGED = "merged"

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
    # § 6.2.9–6.2.10: created with only an ID or a contact and a request to the profile; not
    # ready for operations until it holds the minimum of § 6.2.7–6.2.8.
    provisional = models.BooleanField(default=False)
    merged_into = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    merged_at = models.DateTimeField(null=True, blank=True)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "counterparty"
        constraints = [
            models.UniqueConstraint(fields=["company", "id"], name="counterparty_company_id_uniq"),
            # Minimum of § 6.2.7–6.2.8 and § 22.3.3, except a provisional card (§ 6.2.9, § 6.1.13);
            # the contact minimum is checked by the commands.
            models.CheckConstraint(
                condition=(
                    Q(kind="person")
                    & Q(company_name="")
                    & (Q(provisional=True) | (~Q(first_name="") & ~Q(last_name="")))
                )
                | (
                    Q(kind="company")
                    & Q(first_name="")
                    & Q(last_name="")
                    & Q(middle_name="")
                    & (Q(provisional=True) | ~Q(company_name=""))
                ),
                name="counterparty_minimum_by_kind_v2",
            ),
            models.CheckConstraint(
                condition=(Q(state="merged") & Q(merged_into__isnull=False))
                | (~Q(state="merged") & Q(merged_into__isnull=True)),
                name="counterparty_merged_has_main",
            ),
            # One card of the list per profile in a company (§ 6.2.4, § 6.6.6).
            models.UniqueConstraint(
                fields=["company", "linked_user_id"],
                condition=Q(linked_user_id__isnull=False),
                name="counterparty_one_per_user",
            ),
            models.UniqueConstraint(
                fields=["company", "linked_company_id"],
                condition=Q(linked_company_id__isnull=False),
                name="counterparty_one_per_company",
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
        # § 24.1.7: ended by the system verification after a check.
        PLATFORM = "platform"

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
    # § 6.5.5: access to wrongly shown history revoked when correcting a mistake; the record of
    # the correction stays with the company. What was downloaded is not returned.
    access_revoked_at = models.DateTimeField(null=True, blank=True)
    access_revoked_by_user_id = models.UUIDField(null=True, blank=True)
    access_revoke_reason = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        db_table = "profile_link"
        constraints = [
            models.UniqueConstraint(fields=["company", "id"], name="profile_link_company_id_uniq"),
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


class ClientGroup(models.Model):
    """A group of the client base named by the company (§ 6.2.14); a client may be in several.
    Groups limit which discounts are offered (§ 12.5.2)."""

    class State(models.TextChoices):
        ACTIVE = "active"
        ARCHIVED = "archived"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    name = models.CharField(max_length=100)
    state = models.CharField(max_length=10, choices=State.choices, default=State.ACTIVE)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "client_group"
        constraints = [
            models.CheckConstraint(condition=~Q(name=""), name="client_group_name_not_empty"),
        ]

    def __str__(self) -> str:
        return self.name


class ClientGroupMember(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    group = models.ForeignKey(ClientGroup, on_delete=models.PROTECT, related_name="members")
    client = models.ForeignKey(ClientCard, on_delete=models.PROTECT, related_name="group_links")
    added_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "client_group_member"
        constraints = [
            models.UniqueConstraint(fields=["group", "client"], name="client_group_member_uniq"),
        ]

    def __str__(self) -> str:
        return f"{self.group_id}:{self.client_id}"


class ClientRestriction(models.Model):
    """An internal restriction mark of a client (§ 6.7.1, § 23.8.7). The blacklist acts only inside
    the company: new orders and new service operations are forbidden, the start of an earlier
    booking too; started operations go on, settlements and refunds are not blocked. Setting and
    lifting need different rights; both need a reason and keep the author and the time. Other
    kinds of marks are OPEN."""

    class Kind(models.TextChoices):
        BLACKLIST = "blacklist"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    client = models.ForeignKey(ClientCard, on_delete=models.PROTECT, related_name="restrictions")
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.BLACKLIST)
    reason = models.CharField(max_length=1000)
    added_by_user_id = models.UUIDField()
    added_at = models.DateTimeField(default=now)
    lifted_reason = models.CharField(max_length=1000, blank=True, default="")
    lifted_by_user_id = models.UUIDField(null=True, blank=True)
    lifted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "client_restriction"
        constraints = [
            models.CheckConstraint(condition=~Q(reason=""), name="client_restriction_reason"),
            models.CheckConstraint(
                condition=(
                    Q(lifted_at__isnull=True)
                    & Q(lifted_by_user_id__isnull=True)
                    & Q(lifted_reason="")
                )
                | (
                    Q(lifted_at__isnull=False)
                    & Q(lifted_by_user_id__isnull=False)
                    & ~Q(lifted_reason="")
                ),
                name="client_restriction_lift_complete",
            ),
            models.UniqueConstraint(
                fields=["client", "kind"],
                condition=Q(lifted_at__isnull=True),
                name="client_restriction_one_active",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind}:{self.client_id}"


class CardMerge(models.Model):
    """A duplicate card merged into the main card of the same list of the company (§ 6.2.3,
    § 6.1.13, § 6.5.2). The duplicate is not rewritten: it gets the state «merged» and refers to
    the main card; the records of other modules keep their card and are shown from the main one.
    `chosen` keeps the values taken from the duplicate and `before` the values of both cards, so a
    split restores them (§ 6.5.3)."""

    class CardType(models.TextChoices):
        CLIENT = "client"
        COUNTERPARTY = "counterparty"

    class State(models.TextChoices):
        MERGED = "merged"
        SPLIT = "split"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    card_type = models.CharField(max_length=20, choices=CardType.choices)
    primary_client = models.ForeignKey(
        ClientCard, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    duplicate_client = models.ForeignKey(
        ClientCard, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    primary_counterparty = models.ForeignKey(
        Counterparty, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    duplicate_counterparty = models.ForeignKey(
        Counterparty, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    state = models.CharField(max_length=10, choices=State.choices, default=State.MERGED)
    reason = models.TextField(blank=True, default="")
    chosen = models.JSONField(default=dict, blank=True)
    before = models.JSONField(default=dict, blank=True)
    # The profile the main card took from the duplicate, if any: {"user": id} or {"company": id}.
    moved_link = models.JSONField(default=dict, blank=True)
    merged_by_user_id = models.UUIDField()
    merged_at = models.DateTimeField(default=now)
    split_by_user_id = models.UUIDField(null=True, blank=True)
    split_at = models.DateTimeField(null=True, blank=True)
    split_reason = models.TextField(blank=True, default="")
    # The records made after the merge and the card each was assigned to by hand at the split.
    assignments = models.JSONField(default=list, blank=True)
    # § 6.5.5: links whose access to the history of the other card ended with the split.
    revoked = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "card_merge"
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(card_type="client")
                    & Q(primary_client__isnull=False)
                    & Q(duplicate_client__isnull=False)
                    & Q(primary_counterparty__isnull=True)
                    & Q(duplicate_counterparty__isnull=True)
                )
                | (
                    Q(card_type="counterparty")
                    & Q(primary_client__isnull=True)
                    & Q(duplicate_client__isnull=True)
                    & Q(primary_counterparty__isnull=False)
                    & Q(duplicate_counterparty__isnull=False)
                ),
                name="card_merge_one_list",
            ),
            models.CheckConstraint(
                condition=~Q(primary_client=models.F("duplicate_client"))
                & ~Q(primary_counterparty=models.F("duplicate_counterparty")),
                name="card_merge_two_cards",
            ),
            models.UniqueConstraint(
                fields=["duplicate_client"],
                condition=Q(state="merged"),
                name="card_merge_one_current_client",
            ),
            models.UniqueConstraint(
                fields=["duplicate_counterparty"],
                condition=Q(state="merged"),
                name="card_merge_one_current_counterparty",
            ),
        ]

    def __str__(self) -> str:
        return f"merge {self.id}"


class ProfileDataRequest(models.Model):
    """A request of fresh data from the linked profile (§ 6.4.4), sent only when the client asks
    for a service: never automatically or periodically. The recipient chooses what to give; the
    answer is a new snapshot of the link, it does not change the working fields by itself
    (§ 6.4.2–6.4.3)."""

    class State(models.TextChoices):
        PENDING = "pending"
        ACCEPTED = "accepted"
        DECLINED = "declined"
        CANCELLED = "cancelled"
        EXPIRED = "expired"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    link = models.ForeignKey(ProfileLink, on_delete=models.PROTECT, related_name="data_requests")
    # What the client asked for when the request was sent (the occasion of § 6.4.4).
    occasion = models.CharField(max_length=500)
    state = models.CharField(max_length=10, choices=State.choices, default=State.PENDING)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    expires_at = models.DateTimeField()
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by_user_id = models.UUIDField(null=True, blank=True)
    shared = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "profile_data_request"
        constraints = [
            models.CheckConstraint(condition=~Q(occasion=""), name="profile_data_request_occasion"),
            models.UniqueConstraint(
                fields=["link"],
                condition=Q(state="pending"),
                name="profile_data_request_one_pending",
            ),
        ]

    def __str__(self) -> str:
        return f"data request {self.id}"
