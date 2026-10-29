"""Permission catalog and system permission sets of stage 1 (ТЗ гл. 23).

Codes are the technical form of the rights of chapter 23 (docs/stage1/STAGE1_COMMANDS_API.md).
The lists seed the `permission`, `permission_set` and `permission_set_item` tables; changes are
made with a new data migration. An assignment keeps its own copy of the codes, so a changed set
does not change the access of employees by itself (§ 23.1.3, § 23.1.15).
"""

PERMISSIONS: dict[str, str] = {
    "company.settings": "company",
    "employee.invite": "employee",
    "employee.rights.manage": "employee",
    "employee.journal.view": "employee",
    "employee.access.block": "employee",
    "employee.access.restore": "employee",
    "finance.cash_account.manage": "finance",
    "finance.payment.receive": "finance",
    # Finance stage (§ 23.7.1, § 23.2.7): balances and records of accounts, payouts, articles.
    # `income.record` — a manual receipt without an order — is a PROPOSAL.
    "finance.account.view": "finance",
    "finance.income.record": "finance",
    "finance.payout": "finance",
    "finance.article.manage": "finance",
    # Transfers between own accounts (§ 23.2.7) and a manual exchange rate (§ 23.7.6). Who besides
    # the administrator gets the manual rate is OPEN.
    "finance.transfer": "finance",
    "finance.rate.override": "finance",
    # Settlements with parties (§ 23.2.7, § 23.7.1): initial obligations and results of returns
    # to suppliers, set-off of available funds, corrections of posted records. The split into
    # these three rights is a PROPOSAL.
    "finance.settlement.manage": "finance",
    "finance.offset": "finance",
    "finance.correct": "finance",
    # Deposits (§ 14.3.3, § 14.7): one right for all but the receipt (customer's decision of
    # 26.10.2026); the receipt is in finance.payment.receive.
    "finance.deposit.manage": "finance",
    "tariff.view": "tariff",
    "tariff.create": "tariff",
    "tariff.edit": "tariff",
    "tariff.archive": "tariff",
    "vehicle.view": "vehicle",
    "vehicle.create": "vehicle",
    "vehicle.edit": "vehicle",
    "vehicle.move": "vehicle",
    # Decision of 24.10.2026: a separate right; only in «Company administrator» (PROPOSAL).
    "vehicle.transfer_to_warehouse": "vehicle",
    # Warehouse (§ 23.7.3–23.7.5, § 23.2.6). Scope — the warehouse of a location. `catalog.manage`
    # (creating and changing items) is a PROPOSAL: the ТЗ lets an item be created at receipt.
    "warehouse.view": "warehouse",
    "warehouse.catalog.manage": "warehouse",
    "warehouse.purchase": "warehouse",
    "warehouse.receive": "warehouse",
    "warehouse.issue": "warehouse",
    "warehouse.move": "warehouse",
    "warehouse.repack": "warehouse",
    "warehouse.reserve": "warehouse",
    "warehouse.count": "warehouse",
    "warehouse.adjust.confirm": "warehouse",
    "warehouse.write_off": "warehouse",
    "warehouse.correct": "warehouse",
    "warehouse.tags.edit": "warehouse",
    "warehouse.sell": "warehouse",
    "warehouse.sale.correct": "warehouse",
    "warehouse.price.set": "warehouse",
    "warehouse.price.policy": "warehouse",
    # Import (§ 22.5.1): running it, cancelling a posted one, seeing the history and files.
    "import.run": "import",
    "import.cancel": "import",
    "import.history.view": "import",
    "client.find": "client",
    "client.list": "client",
    "client.create": "client",
    "client.edit": "client",
    # Counterparties (§ 23.8.2), the minimal list of the warehouse stage.
    "counterparty.view": "counterparty",
    "counterparty.create": "counterparty",
    "counterparty.edit": "counterparty",
    "counterparty.archive": "counterparty",
    "counterparty.restore": "counterparty",
    "rental.view": "rental",
    # Bookings (§ 14.1, § 23.2.2): create, change, cancel, auto-cancel moment — one right
    # (customer's decision of 26.10.2026).
    "rental.book": "rental",
    "rental.issue": "rental",
    # § 14.3.7, § 23.4.7: permitting an issue with unmet requirements in advance and deciding on
    # an exception recorded after the transfer are separate rights (customer's decision В8).
    "rental.exception.authorize": "rental",
    "rental.exception.decide": "rental",
    "rental.accept_return": "rental",
    # § 13.5.1: fuel or another manual charge in a rental (customer's decision Т6).
    "rental.manual_charge": "rental",
    "rental.complete_formalization": "rental",
    # § 23.4.3 (customer's decision П12): extension, change of the tariff and terms, reopening.
    "rental.extend": "rental",
    "rental.change_terms": "rental",
    "rental.reopen": "rental",
    # § 12.3.7 (customer's decisions З5, З6): annulling a wrong rental, moving a rental to another
    # order of the client.
    "rental.annul": "rental",
    "rental.transfer": "rental",
    "inspection.create": "inspection",
    "inspection.conduct": "inspection",
    "order.view": "order",
    "order.create": "order",
    "order.edit": "order",
    "order.close": "order",
    "order.close_with_debt": "order",
    # § 12.2.3: cancelling the closing of an order is a separate right.
    "order.reopen": "order",
    # § 12.2.1 (customer's decision З4): cancelling a whole order.
    "order.cancel": "order",
    # Service (§ 23.6.4; customer's decisions С-6, С-7 of 29.10.2026). `service.work` covers
    # starting and finishing one's own work and taking a free one; `service.complete` marks the
    # works of an operation done (the order then closes by «Close orders»); `service.price`
    # changes the price of a work in an order (§ 23.2.3).
    "service.view": "service",
    "service.create": "service",
    "service.cancel": "service",
    "service.accept": "service",
    "service.place": "service",
    "service.assign": "service",
    "service.work": "service",
    "service.work.others": "service",
    "service.complete": "service",
    "service.price": "service",
    "service.catalog": "service",
}

COMPANY_ADMIN = "company_admin"
RENTAL_MANAGER = "rental_manager"
DRIVER = "driver"
ACCOUNTANT = "accountant"
STOREKEEPER = "storekeeper"
SERVICE_MANAGER = "service_manager"
MECHANIC = "mechanic"

SYSTEM_SETS: dict[str, tuple[str, ...]] = {
    # § 23.2.1: full operational access, settings and employee management.
    COMPANY_ADMIN: tuple(PERMISSIONS),
    # § 23.2.2. Whether order.close_with_debt belongs here is OPEN; until decided it does not.
    RENTAL_MANAGER: (
        "vehicle.view",
        # Decision 23.10.2026: the rental manager moves vehicles between locations.
        "vehicle.move",
        "client.find",
        "client.list",
        "client.create",
        "client.edit",
        "rental.view",
        "rental.book",
        "rental.issue",
        "rental.accept_return",
        "rental.manual_charge",
        "rental.complete_formalization",
        # § 23.2.2. Whether rental.change_terms belongs here is OPEN; until decided it does not.
        "rental.extend",
        "inspection.create",
        "inspection.conduct",
        "order.view",
        "order.create",
        "order.edit",
        "order.close",
        "order.cancel",
        "finance.payment.receive",
        "tariff.view",
    ),
    # § 23.2.5: no permission of stage 1.
    DRIVER: (),
    # § 23.2.7; § 23.7.4: the accountant sets selling prices.
    ACCOUNTANT: (
        "finance.payment.receive",
        "finance.account.view",
        "finance.income.record",
        "finance.payout",
        "finance.article.manage",
        "finance.transfer",
        "finance.settlement.manage",
        "finance.offset",
        "finance.correct",
        "finance.deposit.manage",
        "order.view",
        "rental.view",
        "client.find",
        "warehouse.price.set",
    ),
    # § 23.2.6: not purchases, confirmation of adjustments, corrections, sale or price policy.
    STOREKEEPER: (
        "warehouse.view",
        "warehouse.catalog.manage",
        "warehouse.receive",
        "warehouse.issue",
        "warehouse.move",
        "warehouse.repack",
        "warehouse.reserve",
        "warehouse.count",
        "warehouse.write_off",
        "warehouse.tags.edit",
        "warehouse.price.set",
        # PROPOSAL: a missing supplier is added right at a receipt (§ 11.2.8).
        "counterparty.view",
        "counterparty.create",
    ),
    # § 23.2.3. Entering a result for another executor is not listed there; closing the order is
    # not either (TODO(spec): whether the service manager closes orders).
    SERVICE_MANAGER: (
        "vehicle.view",
        "client.find",
        "client.list",
        "client.create",
        "client.edit",
        "counterparty.view",
        "counterparty.create",
        "counterparty.edit",
        "order.view",
        "order.create",
        "order.edit",
        "finance.payment.receive",
        "service.view",
        "service.create",
        "service.cancel",
        "service.accept",
        "service.place",
        "service.assign",
        "service.work",
        "service.complete",
        "service.price",
        "service.catalog",
    ),
    # § 23.2.4: own works, the post and the general zone, inspections; no prices or payments.
    MECHANIC: (
        "vehicle.view",
        "inspection.create",
        "inspection.conduct",
        "service.view",
        "service.place",
        "service.work",
    ),
}

# Especially broad authority (§ 23.3.5): full access to the company and management of employee
# rights. At stage 1 only the top-authority director assigns it (decision 22.10.2026).
BROAD_SETS = frozenset({COMPANY_ADMIN})
BROAD_PERMISSIONS = frozenset({"employee.rights.manage"})
