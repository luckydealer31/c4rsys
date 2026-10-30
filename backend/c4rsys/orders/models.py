"""Orders (ТЗ § 12.1–12.2)."""

from django.db import models
from django.db.models import Q
from django.utils.timezone import now

from c4rsys.companies.models import Company
from c4rsys.platform.ids import uuid7


class Order(models.Model):
    """An order of the company with a number sequential inside it (§ 12.1.1).

    Operations (rentals, later services) point to the order; they are not stored here. «Ready to
    close» is not a stored state: it follows from the operations (§ 12.1.9, § 12.2.3). A preliminary
    order (§ 12.1.7, § 12.2.2) is discussed with the client and charges nothing; «Form the order»
    turns the same row into an open one. The table is named `orders`: ORDER is an SQL keyword.
    """

    class State(models.TextChoices):
        PRELIMINARY = "preliminary"
        OPEN = "open"
        CLOSED = "closed"
        CANCELLED = "cancelled"
        TERMINATED = "terminated"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    number = models.PositiveBigIntegerField()
    client = models.ForeignKey(
        "parties.ClientCard", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    is_internal = models.BooleanField(default=False)
    state = models.CharField(max_length=20, choices=State.choices, default=State.OPEN)
    responsible_employment_id = models.UUIDField(null=True, blank=True)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by_user_id = models.UUIDField(null=True, blank=True)
    # § 12.2.1: a cancellation before the operations start, with its reason (decision З4).
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=2000, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "orders"
        constraints = [
            models.UniqueConstraint(fields=["company", "id"], name="orders_company_id_uniq"),
            models.UniqueConstraint(
                fields=["company", "number"], name="orders_company_number_uniq"
            ),
            # An internal order is the company's own (§ 12.1.3); a client order created by a
            # booking may wait for its client, «client not specified» (§ 12.1.8).
            models.CheckConstraint(
                condition=Q(is_internal=False) | Q(client__isnull=True),
                name="orders_client_or_internal",
            ),
            models.CheckConstraint(
                condition=~Q(state="closed") | Q(closed_at__isnull=False),
                name="orders_closed_has_time",
            ),
            models.CheckConstraint(
                condition=~Q(state="cancelled") | Q(cancelled_at__isnull=False),
                name="orders_cancelled_has_time",
            ),
        ]

    def __str__(self) -> str:
        return f"order {self.number}"


class OrderPolicy(models.Model):
    """«Agree additions with the client» of the company (§ 12.3.5; decision З9): off by default.
    A change of the cost needs no agreement; the client is only told (decision В-1)."""

    company = models.OneToOneField(Company, on_delete=models.PROTECT, primary_key=True)
    require_approval = models.BooleanField(default=False)
    updated_by_user_id = models.UUIDField()
    updated_at = models.DateTimeField(default=now)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "order_policy"

    def __str__(self) -> str:
        return f"order policy {self.company_id}"


class OrderApproval(models.Model):
    """A position added to an order in work that waits for the client's answer (§ 12.3.1–12.3.5;
    decisions З9, З10). The answer is the client's own (in the cabinet) or recorded by an employee
    with the date, the way of contact and a comment; the history tells them apart (§ 12.3.4).
    «Do without the client's confirmation» lets the position start; it is not the client's
    consent (§ 12.3.2)."""

    class State(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        DECLINED = "declined"

    class Source(models.TextChoices):
        CLIENT = "client"
        EMPLOYEE = "employee"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="approvals")
    item_type = models.CharField(max_length=20)
    item_id = models.UUIDField()
    title = models.CharField(max_length=300, blank=True, default="")
    state = models.CharField(max_length=10, choices=State.choices, default=State.PENDING)
    requested_at = models.DateTimeField(default=now)
    requested_by_user_id = models.UUIDField()
    proceed_at = models.DateTimeField(null=True, blank=True)
    proceed_by_user_id = models.UUIDField(null=True, blank=True)
    answered_at = models.DateTimeField(null=True, blank=True)
    answer_source = models.CharField(max_length=10, choices=Source.choices, blank=True, default="")
    answered_by_user_id = models.UUIDField(null=True, blank=True)
    contact_method = models.CharField(max_length=20, blank=True, default="")
    comment = models.CharField(max_length=2000, blank=True, default="")
    document_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "order_approval"
        constraints = [
            models.UniqueConstraint(
                fields=["item_type", "item_id"], name="order_approval_item_uniq"
            ),
            models.CheckConstraint(
                condition=Q(state="pending") | Q(answered_at__isnull=False),
                name="order_approval_answer_has_time",
            ),
        ]

    def __str__(self) -> str:
        return f"approval {self.item_type}:{self.item_id}"
