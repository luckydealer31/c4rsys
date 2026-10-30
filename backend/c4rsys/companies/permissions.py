"""Permission catalog and system permission sets of stage 1 (ТЗ гл. 23).

Codes are the technical form of the rights of chapter 23 (docs/stage1/STAGE1_COMMANDS_API.md).
The lists seed the `permission`, `permission_set` and `permission_set_item` tables; changes are
made with a new data migration. An assignment keeps its own copy of the codes, so a changed set
does not change the access of employees by itself (§ 23.1.3, § 23.1.15).
"""

PERMISSIONS: dict[str, str] = {
    "company.settings": "company",
    # § 6.3.8: answering another company's request to link with this company's profile. The
    # top-authority director answers without it; only in «Company administrator» (PROPOSAL).
    "company.link.answer": "company",
    # § 22.6.1 (decision Б-6): the full export of the company's data; only in «Company
    # administrator».
    "data.export": "company",
    # § 3.6.1: deactivation and restoration of the company with the owners' approval. One right
    # for both and only in «Company administrator» (PROPOSAL).
    "company.deactivate": "company",
    # § 24.3.2: the separate security right — notices of finished privileged sessions of the
    # c4rsys staff and their journal. Only in «Company administrator» (PROPOSAL).
    "company.security": "company",
    "employee.invite": "employee",
    "employee.rights.manage": "employee",
    "employee.journal.view": "employee",
    "employee.access.block": "employee",
    "employee.access.restore": "employee",
    # § 23.3.9, § 23.3.1 (block 10, part B): editing the employee card, managing the positions
    # handbook, terminating access and managing the organisation structure are separate rights;
    # only in «Company administrator» (PROPOSAL).
    "employee.card.edit": "employee",
    "employee.positions.manage": "employee",
    "employee.access.terminate": "employee",
    "org.manage": "org",
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
    # § 23.4.1–23.4.2: cancelling before the start, terminating a started one and recognising a
    # saved one invalid are separate rights; § 23.3.7: the policies (and the templates with their
    # photo requirements, § 10.3.1) are managed by a right of their own.
    "inspection.cancel": "inspection",
    "inspection.terminate": "inspection",
    "inspection.invalidate": "inspection",
    "inspection.policy.manage": "inspection",
    "order.view": "order",
    "order.create": "order",
    "order.edit": "order",
    "order.close": "order",
    "order.close_with_debt": "order",
    # § 12.2.3: cancelling the closing of an order is a separate right.
    "order.reopen": "order",
    # § 12.2.1 (customer's decision З4): cancelling a whole order.
    "order.cancel": "order",
    # § 12.5.2, § 12.5.5 (decision З12): applying discounts within the employee's limit; the
    # catalog of discounts is kept with the company settings (PROPOSAL).
    "order.discount": "order",
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
    # § 16.8.2: opening a finished service or work again and correcting it; ordinary completion
    # does not give it. A separate right assigned to any employee (customer's decision of
    # 30.10.2026); of the system sets only «Company administrator» has it.
    "service.reopen": "service",
    "service.price": "service",
    "service.catalog": "service",
    # § 23.6.4: creating a service at an external counterparty; recording the handover to the
    # contractor and the return (part С4).
    "service.external": "service",
    "service.handover": "service",
    # Delivery and internal move (§ 23.6.6; customer's decision Д-8 of 29.10.2026). `drive` —
    # the driver's own trips: seeing them and recording their start and end (§ 23.2.5).
    "delivery.view": "delivery",
    "delivery.create": "delivery",
    "delivery.edit": "delivery",
    "delivery.assign": "delivery",
    "delivery.take": "delivery",
    "delivery.start": "delivery",
    "delivery.finish": "delivery",
    "delivery.cancel": "delivery",
    "delivery.correct": "delivery",
    "delivery.drive": "delivery",
    # § 15.2.6 (decision Д-5): the catalog of delivery services; changing the substituted price.
    "delivery.catalog": "delivery",
    "delivery.price": "delivery",
    # Documents (§ 23.8.1; customer's decision Р-6 of 29.10.2026): preparing a document does not
    # give the right to sign it for the company.
    "documents.view": "documents",
    "documents.upload": "documents",
    "documents.create": "documents",
    "documents.templates": "documents",
    "documents.archive": "documents",
    "documents.sign": "documents",
    # Work tasks (§ 23.9; customer's decision З-3 of 29.10.2026).
    "tasks.own": "tasks",
    "tasks.queue": "tasks",
    "tasks.create": "tasks",
    "tasks.view_all": "tasks",
    "tasks.manage": "tasks",
    # Reports (§ 23.8.5; customer's decision О-1 of 29.10.2026): viewing and export are separate;
    # the data inside stays limited by the rights of each section (§ 21.1.1).
    "reports.view": "reports",
    "reports.export": "reports",
}

# Decision З-3: the managers get every task right in their area; the others work with their own
# tasks and the queue and create tasks.
TASK_ALL = ("tasks.own", "tasks.queue", "tasks.create", "tasks.view_all", "tasks.manage")
TASK_BASIC = ("tasks.own", "tasks.queue", "tasks.create")

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
        # § 23.2.2: related deliveries and the driver (decision Д-8).
        "delivery.view",
        "delivery.create",
        "delivery.edit",
        "delivery.assign",
        "delivery.cancel",
        "delivery.price",
        # § 23.2.2: documents by ready templates and attached files (decision Р-6).
        "documents.view",
        "documents.upload",
        "documents.create",
        *TASK_ALL,
        "reports.view",
    ),
    # § 23.2.5: only the trips assigned to the driver (decision Д-8).
    DRIVER: ("delivery.drive", *TASK_BASIC),
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
        *TASK_BASIC,
        "reports.view",
        "reports.export",
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
        *TASK_BASIC,
        "reports.view",
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
        # § 23.2.3: «viewing the stock and adding materials from storage to service works»; the
        # issue right is the PROPOSAL for adding them (part С3).
        "warehouse.view",
        "warehouse.issue",
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
        "service.external",
        "service.handover",
        # § 23.2.3: related deliveries and the driver (decision Д-8).
        "delivery.view",
        "delivery.create",
        "delivery.edit",
        "delivery.assign",
        "delivery.cancel",
        # § 23.2.3: documents by templates (decision Р-6).
        "documents.view",
        "documents.upload",
        "documents.create",
        *TASK_ALL,
        "reports.view",
    ),
    # § 23.2.4: own works, the post and the general zone, inspections; no prices or payments.
    MECHANIC: (
        "vehicle.view",
        "inspection.create",
        "inspection.conduct",
        "service.view",
        "service.place",
        "service.work",
        *TASK_BASIC,
    ),
}

# Especially broad authority (§ 23.3.5): full access to the company and management of employee
# rights. At stage 1 only the top-authority director assigns it (decision 22.10.2026).
BROAD_SETS = frozenset({COMPANY_ADMIN})
BROAD_PERMISSIONS = frozenset({"employee.rights.manage"})
