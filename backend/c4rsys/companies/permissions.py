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
    "rental.issue": "rental",
    "rental.accept_return": "rental",
    "rental.complete_formalization": "rental",
    "inspection.create": "inspection",
    "inspection.conduct": "inspection",
    "order.view": "order",
    "order.create": "order",
    "order.edit": "order",
    "order.close": "order",
    "order.close_with_debt": "order",
}

COMPANY_ADMIN = "company_admin"
RENTAL_MANAGER = "rental_manager"
DRIVER = "driver"
ACCOUNTANT = "accountant"
STOREKEEPER = "storekeeper"

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
        "rental.issue",
        "rental.accept_return",
        "rental.complete_formalization",
        "inspection.create",
        "inspection.conduct",
        "order.view",
        "order.create",
        "order.edit",
        "order.close",
        "finance.payment.receive",
        "tariff.view",
    ),
    # § 23.2.5: no permission of stage 1.
    DRIVER: (),
    # § 23.2.7; § 23.7.4: the accountant sets selling prices.
    ACCOUNTANT: (
        "finance.payment.receive",
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
}

# Especially broad authority (§ 23.3.5): full access to the company and management of employee
# rights. At stage 1 only the top-authority director assigns it (decision 22.10.2026).
BROAD_SETS = frozenset({COMPANY_ADMIN})
BROAD_PERMISSIONS = frozenset({"employee.rights.manage"})
