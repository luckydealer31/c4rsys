"""Money of the company: cash desks and bank accounts, income and expense articles, accruals and
money records (ТЗ § 17.2, § 22.1.7)."""

from django.db import models
from django.db.models import Q
from django.utils.timezone import now

from c4rsys.companies.models import Company
from c4rsys.platform.ids import uuid7
from c4rsys.platform.models import Currency


class CashAccount(models.Model):
    """A cash desk or a bank account of the company, not bound to any location (§ 17.2.5,
    clarification of 23.10.2026). Access is set by the employee's rights (§ 23.1.11).

    Created only by hand (§ 22.1.7). A bank account is an accounting object, not a bank
    integration (§ 17.2.9).
    """

    class Type(models.TextChoices):
        CASH = "cash"
        BANK = "bank"

    class State(models.TextChoices):
        ACTIVE = "active"
        ARCHIVED = "archived"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    name = models.CharField(max_length=150)
    type = models.CharField(max_length=10, choices=Type.choices)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    state = models.CharField(max_length=10, choices=State.choices, default=State.ACTIVE)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "cash_account"
        constraints = [
            models.UniqueConstraint(fields=["company", "id"], name="cash_account_company_id_uniq"),
            models.CheckConstraint(condition=~Q(name=""), name="cash_account_name_not_empty"),
        ]

    def __str__(self) -> str:
        return self.name


class FinanceArticle(models.Model):
    """An income or expense article of the company (§ 17.2.8). A starting article has a constant
    `code` and a translated name until the company renames it; own articles have no code. The
    name is shown as entered, without translation. An archived article is not offered for new
    records but stays in the old ones."""

    class Kind(models.TextChoices):
        INCOME = "income"
        EXPENSE = "expense"

    class State(models.TextChoices):
        ACTIVE = "active"
        ARCHIVED = "archived"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    code = models.CharField(max_length=40, blank=True, default="")
    name = models.CharField(max_length=150, blank=True, default="")
    state = models.CharField(max_length=10, choices=State.choices, default=State.ACTIVE)
    created_by_user_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(default=now)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "finance_article"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "id"], name="finance_article_company_id_uniq"
            ),
            models.UniqueConstraint(
                fields=["company", "code"],
                condition=~Q(code=""),
                name="finance_article_company_code_uniq",
            ),
            # A starting article may keep its translated name; an own article needs a name.
            models.CheckConstraint(
                condition=~Q(code="") | ~Q(name=""), name="finance_article_named"
            ),
        ]

    def __str__(self) -> str:
        return self.name or self.code


class ChargeEntry(models.Model):
    """An accrual to a client in an order, append-only (§ 12.2.4, § 17.2.6). The current charge
    of a source (for example the base cost of a rental) is the sum of its entries; a recalculation
    adds the difference as a new entry instead of changing an old one (§ 24.4.6)."""

    class SourceType(models.TextChoices):
        RENTAL_BASE = "rental_base"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    order = models.ForeignKey("orders.Order", on_delete=models.PROTECT, related_name="+")
    client = models.ForeignKey("parties.ClientCard", on_delete=models.PROTECT, related_name="+")
    source_type = models.CharField(max_length=30, choices=SourceType.choices)
    source_id = models.UUIDField()
    amount = models.DecimalField(max_digits=20, decimal_places=4)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    reason = models.CharField(max_length=60)
    calculation = models.JSONField(default=dict)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "charge_entry"
        indexes = [
            models.Index(fields=["company", "order"], name="charge_entry_order_idx"),
            models.Index(fields=["source_type", "source_id"], name="charge_entry_source_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.source_type}:{self.amount} {self.currency_id}"


class MoneyMovement(models.Model):
    """An actual receipt or payout of money, an independent record (§ 17.2.11) with a number
    sequential inside the company (§ 12.2.5). Separate from charges (§ 17.2.6). A posted
    movement is not changed; corrections and refunds are separate records (§ 12.2.5)."""

    class Direction(models.TextChoices):
        IN = "in"
        OUT = "out"

    class Kind(models.TextChoices):
        # A client payment received to orders (§ 12.2.5, § 17.2.10).
        PAYMENT = "payment"
        # The initial balance of an account: not a receipt of money (§ 22.1.7, § 17.2.14).
        OPENING = "opening"
        # A receipt or payout entered by hand, without an order (§ 17.2.2, § 17.2.7).
        MANUAL = "manual"

    class State(models.TextChoices):
        EXPECTED = "expected"
        POSTED = "posted"
        FAILED = "failed"
        CANCELLED = "cancelled"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    number = models.PositiveBigIntegerField()
    cash_account = models.ForeignKey(CashAccount, on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.PAYMENT)
    direction = models.CharField(max_length=3, choices=Direction.choices)
    amount = models.DecimalField(max_digits=20, decimal_places=4)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    client = models.ForeignKey(
        "parties.ClientCard", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    article = models.ForeignKey(
        FinanceArticle, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    # Analytic dimensions of the result (§ 17.2.4, § 17.3.1); they do not multiply the record.
    location = models.ForeignKey(
        "companies.Location", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    context = models.ForeignKey(
        "companies.ActivityContext",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    vehicle = models.ForeignKey(
        "fleet.VehicleCard", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    state = models.CharField(max_length=10, choices=State.choices)
    method = models.CharField(max_length=10)
    note = models.CharField(max_length=500, blank=True, default="")
    posted_at = models.DateTimeField(null=True, blank=True)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "money_movement"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "id"], name="money_movement_company_id_uniq"
            ),
            models.UniqueConstraint(
                fields=["company", "number"], name="money_movement_company_number_uniq"
            ),
            models.CheckConstraint(
                condition=Q(amount__gt=0), name="money_movement_amount_positive"
            ),
            models.CheckConstraint(
                condition=~Q(state="posted") | Q(posted_at__isnull=False),
                name="money_movement_posted_has_time",
            ),
            # One initial balance per account, entered as money on it (§ 22.1.7).
            models.UniqueConstraint(
                fields=["cash_account"],
                condition=Q(kind="opening"),
                name="money_movement_one_opening_per_account",
            ),
            models.CheckConstraint(
                condition=~Q(kind="opening") | (Q(direction="in") & Q(client__isnull=True)),
                name="money_movement_opening_shape",
            ),
            models.CheckConstraint(
                condition=~Q(kind="manual") | Q(article__isnull=False),
                name="money_movement_manual_has_article",
            ),
        ]
        indexes = [
            models.Index(
                fields=["company", "cash_account", "posted_at"],
                name="money_movement_account_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind} {self.number}"


class PaymentAllocation(models.Model):
    """Part of a receipt assigned to an order (§ 12.2.5, § 14.7.3). Active allocations of a
    movement never exceed its amount (checked by the command under the movement's lock). A change
    is a new record; the replaced one stays cancelled in the history (§ 17.2.15)."""

    class Purpose(models.TextChoices):
        ORDER_PAYMENT = "order_payment"
        DEPOSIT = "deposit"

    class State(models.TextChoices):
        ACTIVE = "active"
        CANCELLED = "cancelled"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    money_movement = models.ForeignKey(
        MoneyMovement, on_delete=models.PROTECT, related_name="allocations"
    )
    order = models.ForeignKey("orders.Order", on_delete=models.PROTECT, related_name="+")
    purpose = models.CharField(max_length=20, choices=Purpose.choices)
    amount = models.DecimalField(max_digits=20, decimal_places=4)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    state = models.CharField(max_length=10, choices=State.choices, default=State.ACTIVE)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "payment_allocation"
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="payment_allocation_positive"),
        ]
        indexes = [
            models.Index(fields=["company", "order", "state"], name="payment_allocation_order_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.amount} {self.currency_id} → {self.order_id}"
