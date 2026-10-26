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
        # One side of a transfer between own accounts: neither income nor expense (§ 17.1.6).
        TRANSFER = "transfer"
        # A payment to a supplier (out) or money back from one (in), linked to purchases and
        # receipts (§ 11.2.4, § 11.4.2).
        SUPPLIER = "supplier"
        # Available funds paid back to the client (§ 17.2.10, § 17.2.17).
        REFUND = "refund"
        # A deposit paid back to the client; it is not the client's available funds (§ 14.7.3).
        DEPOSIT = "deposit"
        # A correction: the opposite of a posted record, which stays in history (§ 23.2.7).
        REVERSAL = "reversal"

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
    counterparty = models.ForeignKey(
        "parties.Counterparty", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    article = models.ForeignKey(
        FinanceArticle, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    # A reversal names the record it cancels out; that one stays as it was (§ 23.2.7).
    reverses = models.OneToOneField(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="reversed_by"
    )
    reason = models.CharField(max_length=500, blank=True, default="")
    # A refund in another currency: what leaves the client's funds, by the applied rate
    # (§ 17.2.17). Empty — the same as `amount` and `currency`.
    funds_amount = models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True)
    funds_currency = models.ForeignKey(
        Currency, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    rate_fetch = models.ForeignKey(
        "RateFetch", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    suggested_rate = models.DecimalField(max_digits=28, decimal_places=12, null=True, blank=True)
    applied_rate = models.DecimalField(max_digits=28, decimal_places=12, null=True, blank=True)
    rate_manual = models.BooleanField(default=False)
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
            models.CheckConstraint(
                condition=Q(client__isnull=True) | Q(counterparty__isnull=True),
                name="money_movement_one_party",
            ),
            models.CheckConstraint(
                condition=~Q(kind="supplier") | Q(counterparty__isnull=False),
                name="money_movement_supplier_has_counterparty",
            ),
            models.CheckConstraint(
                condition=~Q(kind="refund") | (Q(direction="out") & Q(client__isnull=False)),
                name="money_movement_refund_shape",
            ),
            models.CheckConstraint(
                condition=~Q(kind="deposit") | (Q(direction="out") & Q(client__isnull=False)),
                name="money_movement_deposit_shape",
            ),
            models.CheckConstraint(
                condition=(Q(kind="reversal") & Q(reverses__isnull=False) & ~Q(reason=""))
                | (~Q(kind="reversal") & Q(reverses__isnull=True)),
                name="money_movement_reversal_shape",
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
    # Part of the receipt, in the currency of the payment.
    amount = models.DecimalField(max_digits=20, decimal_places=4)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    # What it settles in the currency of the order when that differs (§ 17.1.4); empty — the same
    # as `amount` and `currency`.
    settled_amount = models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True)
    settled_currency = models.ForeignKey(
        Currency, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    rate_fetch = models.ForeignKey(
        "RateFetch", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    suggested_rate = models.DecimalField(max_digits=28, decimal_places=12, null=True, blank=True)
    applied_rate = models.DecimalField(max_digits=28, decimal_places=12, null=True, blank=True)
    rate_manual = models.BooleanField(default=False)
    state = models.CharField(max_length=10, choices=State.choices, default=State.ACTIVE)
    # The allocation this one replaces when the link to an order is changed (§ 17.2.11).
    replaces = models.OneToOneField(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="replaced_by"
    )
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "payment_allocation"
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="payment_allocation_positive"),
            models.CheckConstraint(
                condition=(
                    Q(settled_amount__isnull=True)
                    & Q(settled_currency__isnull=True)
                    & Q(applied_rate__isnull=True)
                )
                | (
                    Q(settled_amount__gt=0)
                    & Q(settled_currency__isnull=False)
                    & Q(applied_rate__gt=0)
                ),
                name="payment_allocation_exchange_complete",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "order", "state"], name="payment_allocation_order_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.amount} {self.currency_id} → {self.order_id}"


class RateFetch(models.Model):
    """One download of reference exchange rates from the system source (§ 17.1.6). System data,
    not of a company; a new download adds rows and never changes old ones."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    source = models.CharField(max_length=40)
    base = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    # Time of the rates at the source and the time c4rsys received them.
    source_time = models.DateTimeField()
    fetched_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "rate_fetch"
        indexes = [models.Index(fields=["fetched_at"], name="rate_fetch_time_idx")]

    def __str__(self) -> str:
        return f"{self.source} {self.fetched_at:%Y-%m-%d %H:%M}"


class ExchangeRate(models.Model):
    """1 `base` = `rate` × `quote` at the time of its download (§ 17.1.6)."""

    id = models.BigAutoField(primary_key=True)
    fetch = models.ForeignKey(RateFetch, on_delete=models.PROTECT, related_name="rates")
    quote = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    rate = models.DecimalField(max_digits=28, decimal_places=12)

    class Meta:
        db_table = "exchange_rate"
        constraints = [
            models.UniqueConstraint(
                fields=["fetch", "quote"], name="exchange_rate_fetch_quote_uniq"
            ),
            models.CheckConstraint(condition=Q(rate__gt=0), name="exchange_rate_positive"),
        ]

    def __str__(self) -> str:
        return f"{self.quote_id} {self.rate}"


class Transfer(models.Model):
    """A transfer between own accounts: a linked payout from the source account and receipt to
    the target, both amounts and the applied rate kept (§ 17.1.6). Not income or expense; a
    posted transfer is not changed — a mistake is corrected by a reverse transfer."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    out_movement = models.OneToOneField(
        MoneyMovement, on_delete=models.PROTECT, related_name="transfer_out"
    )
    in_movement = models.OneToOneField(
        MoneyMovement, on_delete=models.PROTECT, related_name="transfer_in"
    )
    rate_fetch = models.ForeignKey(
        RateFetch, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    suggested_rate = models.DecimalField(max_digits=28, decimal_places=12, null=True, blank=True)
    applied_rate = models.DecimalField(max_digits=28, decimal_places=12)
    rate_manual = models.BooleanField(default=False)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "money_transfer"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "id"], name="money_transfer_company_id_uniq"
            ),
            models.CheckConstraint(
                condition=Q(applied_rate__gt=0), name="money_transfer_rate_positive"
            ),
        ]

    def __str__(self) -> str:
        return f"transfer {self.out_movement_id} → {self.in_movement_id}"


class SupplierAllocation(models.Model):
    """Part of a supplier payment (or of money back from a supplier) linked to a purchase or a
    receipt (§ 11.2.4): prepayment before receipt, partial payment and payment of received goods.
    A change is a new record; the replaced one stays cancelled."""

    class State(models.TextChoices):
        ACTIVE = "active"
        CANCELLED = "cancelled"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    money_movement = models.ForeignKey(
        MoneyMovement, on_delete=models.PROTECT, related_name="supplier_allocations"
    )
    purchase = models.ForeignKey(
        "warehouse.PurchaseOrder", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    receipt = models.ForeignKey(
        "warehouse.Receipt", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    amount = models.DecimalField(max_digits=20, decimal_places=4)
    state = models.CharField(max_length=10, choices=State.choices, default=State.ACTIVE)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "supplier_allocation"
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="supplier_allocation_positive"),
            models.CheckConstraint(
                condition=Q(purchase__isnull=True) ^ Q(receipt__isnull=True),
                name="supplier_allocation_one_target",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.amount} → {self.purchase_id or self.receipt_id}"


class SettlementEntry(models.Model):
    """An obligation between the company and a party that is not a movement of money
    (§ 17.2.3): an initial debt or advance when accounting starts in c4rsys (§ 17.2.14), or a
    smaller debt to a supplier after goods were returned (§ 11.4.2). Never changed; a mistake is
    corrected by a cancelling entry."""

    class Kind(models.TextChoices):
        OPENING = "opening"
        RETURN_CREDIT = "return_credit"
        CANCELLATION = "cancellation"

    class Debtor(models.TextChoices):
        PARTY = "party"  # the client or counterparty owes the company
        COMPANY = "company"  # the company owes the party (incl. a client's advance)

    class ReturnResult(models.TextChoices):
        # What the supplier does for the returned goods (§ 11.4.2). The debt goes down in each
        # case; money back is a separate receipt, a replacement comes as a new receipt.
        MONEY_BACK = "money_back"
        DEBT_REDUCED = "debt_reduced"
        REPLACEMENT = "replacement"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    client = models.ForeignKey(
        "parties.ClientCard", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    counterparty = models.ForeignKey(
        "parties.Counterparty", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    debtor = models.CharField(max_length=10, choices=Debtor.choices)
    amount = models.DecimalField(max_digits=20, decimal_places=4)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    as_of = models.DateField()
    # The return a credit settles, and the entry a cancellation cancels.
    stock_operation = models.OneToOneField(
        "warehouse.StockOperation",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    return_result = models.CharField(
        max_length=20, choices=ReturnResult.choices, blank=True, default=""
    )
    # A cancellation has the opposite debtor, so the sum of entries is the obligation.
    cancels = models.OneToOneField(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="cancelled_by"
    )
    note = models.CharField(max_length=500, blank=True, default="")
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "settlement_entry"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "id"], name="settlement_entry_company_id_uniq"
            ),
            models.CheckConstraint(condition=Q(amount__gt=0), name="settlement_entry_positive"),
            models.CheckConstraint(
                condition=Q(client__isnull=True) ^ Q(counterparty__isnull=True),
                name="settlement_entry_one_party",
            ),
            models.CheckConstraint(
                condition=~Q(kind="return_credit")
                | (
                    Q(stock_operation__isnull=False)
                    & Q(counterparty__isnull=False)
                    & Q(debtor="party")
                ),
                name="settlement_entry_return_credit_shape",
            ),
            models.CheckConstraint(
                condition=Q(kind="cancellation", cancels__isnull=False)
                | (~Q(kind="cancellation") & Q(cancels__isnull=True)),
                name="settlement_entry_cancellation_shape",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind} {self.amount} {self.currency_id}"


class Offset(models.Model):
    """A set-off of the client's available funds against the debt of an order (§ 17.2.10,
    § 17.2.13): it lowers both by the same amount without a new receipt of money. A correction
    cancels it and creates a new one that names it; cancelled ones stay in history (§ 17.2.15).
    Funds in another currency settle the order by the applied rate (§ 17.1.4)."""

    class State(models.TextChoices):
        ACTIVE = "active"
        CANCELLED = "cancelled"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    client = models.ForeignKey("parties.ClientCard", on_delete=models.PROTECT, related_name="+")
    # What is settled: the debt of an order, or an initial debt of the client (§ 17.2.14).
    order = models.ForeignKey(
        "orders.Order", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    opening = models.ForeignKey(
        SettlementEntry, on_delete=models.PROTECT, null=True, blank=True, related_name="offsets"
    )
    # Taken from the client's funds, in their currency.
    amount = models.DecimalField(max_digits=20, decimal_places=4)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    settled_amount = models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True)
    settled_currency = models.ForeignKey(
        Currency, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    rate_fetch = models.ForeignKey(
        RateFetch, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    suggested_rate = models.DecimalField(max_digits=28, decimal_places=12, null=True, blank=True)
    applied_rate = models.DecimalField(max_digits=28, decimal_places=12, null=True, blank=True)
    rate_manual = models.BooleanField(default=False)
    state = models.CharField(max_length=10, choices=State.choices, default=State.ACTIVE)
    replaces = models.OneToOneField(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="replaced_by"
    )
    comment = models.CharField(max_length=500, blank=True, default="")
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    cancelled_by_user_id = models.UUIDField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "finance_offset"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "id"], name="finance_offset_company_id_uniq"
            ),
            models.CheckConstraint(condition=Q(amount__gt=0), name="finance_offset_positive"),
            models.CheckConstraint(
                condition=Q(order__isnull=True) ^ Q(opening__isnull=True),
                name="finance_offset_one_target",
            ),
            models.CheckConstraint(
                condition=(
                    Q(settled_amount__isnull=True)
                    & Q(settled_currency__isnull=True)
                    & Q(applied_rate__isnull=True)
                )
                | (
                    Q(settled_amount__gt=0)
                    & Q(settled_currency__isnull=False)
                    & Q(applied_rate__gt=0)
                ),
                name="finance_offset_exchange_complete",
            ),
            models.CheckConstraint(
                condition=Q(state="active", cancelled_at__isnull=True)
                | Q(state="cancelled", cancelled_at__isnull=False),
                name="finance_offset_cancelled_at",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "client", "state"], name="finance_offset_client_idx"),
            models.Index(fields=["company", "order", "state"], name="finance_offset_order_idx"),
        ]

    def __str__(self) -> str:
        return f"offset {self.amount} {self.currency_id} → {self.order_id}"


class ExpenseShare(models.Model):
    """Part of one expense attributed to a vehicle or an order (§ 17.3.3). The shares of a record
    add up to its amount; they are analytic and do not multiply the payout. A new split cancels
    the previous shares, which stay in history."""

    class State(models.TextChoices):
        ACTIVE = "active"
        CANCELLED = "cancelled"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    money_movement = models.ForeignKey(
        MoneyMovement, on_delete=models.PROTECT, related_name="shares"
    )
    vehicle = models.ForeignKey(
        "fleet.VehicleCard", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    order = models.ForeignKey(
        "orders.Order", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    amount = models.DecimalField(max_digits=20, decimal_places=4)
    state = models.CharField(max_length=10, choices=State.choices, default=State.ACTIVE)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "expense_share"
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="expense_share_positive"),
            models.CheckConstraint(
                condition=Q(vehicle__isnull=True) ^ Q(order__isnull=True),
                name="expense_share_one_object",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.amount} → {self.vehicle_id or self.order_id}"


class DepositEntry(models.Model):
    """A movement of the deposit of a rental (ТЗ § 14.7, § 14.3.3), in the deposit's currency.

    The deposit is held apart from the client's available funds and is not income (§ 6.7.2,
    § 14.7.3). Received — part of a receipt of money; to balance — moved to the client's
    settlement balance (by the term or earlier by hand); hold / release — the part held by a
    claim; offset — set off against a debt; refund — paid back by a payout; waiver — the conflict
    of a missing deposit ended without money. Entries are never changed."""

    class Kind(models.TextChoices):
        RECEIVED = "received"
        TO_BALANCE = "to_balance"
        HOLD = "hold"
        RELEASE = "release"
        OFFSET = "offset"
        REFUND = "refund"
        WAIVER = "waiver"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    rental = models.ForeignKey("rentals.Rental", on_delete=models.PROTECT, related_name="+")
    client = models.ForeignKey("parties.ClientCard", on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    amount = models.DecimalField(max_digits=20, decimal_places=4)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    # A part of a receipt in another currency, or a refund paid in another currency (§ 17.1.4).
    money_amount = models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True)
    money_currency = models.ForeignKey(
        Currency, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    rate_fetch = models.ForeignKey(
        RateFetch, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    suggested_rate = models.DecimalField(max_digits=28, decimal_places=12, null=True, blank=True)
    applied_rate = models.DecimalField(max_digits=28, decimal_places=12, null=True, blank=True)
    rate_manual = models.BooleanField(default=False)
    money_movement = models.ForeignKey(
        MoneyMovement, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    order = models.ForeignKey(
        "orders.Order", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    reason = models.CharField(max_length=500, blank=True, default="")
    # By the term of the deposit (§ 14.7.1), without an employee.
    automatic = models.BooleanField(default=False)
    created_by_user_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "deposit_entry"
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="deposit_entry_positive"),
            models.CheckConstraint(
                condition=~Q(kind__in=["received", "refund"]) | Q(money_movement__isnull=False),
                name="deposit_entry_money_has_record",
            ),
            models.CheckConstraint(
                condition=~Q(kind="offset") | Q(order__isnull=False),
                name="deposit_entry_offset_has_order",
            ),
            models.CheckConstraint(
                condition=~Q(kind__in=["hold", "release", "waiver"]) | ~Q(reason=""),
                name="deposit_entry_reason_required",
            ),
            models.CheckConstraint(
                condition=Q(automatic=False) | Q(kind="to_balance"),
                name="deposit_entry_automatic_only_to_balance",
            ),
            models.CheckConstraint(
                condition=(Q(money_amount__isnull=True) & Q(applied_rate__isnull=True))
                | (Q(money_amount__gt=0) & Q(money_currency__isnull=False) & Q(applied_rate__gt=0)),
                name="deposit_entry_exchange_complete",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "rental"], name="deposit_entry_rental_idx"),
            models.Index(fields=["company", "client"], name="deposit_entry_client_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.kind} {self.amount} {self.currency_id}"
