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
    "client.find": "client",
    "client.list": "client",
    "client.create": "client",
    "client.edit": "client",
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

SYSTEM_SETS: dict[str, tuple[str, ...]] = {
    # § 23.2.1: full operational access, settings and employee management.
    COMPANY_ADMIN: tuple(PERMISSIONS),
    # § 23.2.2. Whether order.close_with_debt belongs here is OPEN; until decided it does not.
    RENTAL_MANAGER: (
        "vehicle.view",
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
    # § 23.2.7.
    ACCOUNTANT: ("finance.payment.receive", "order.view", "rental.view", "client.find"),
}

# Especially broad authority (§ 23.3.5): full access to the company and management of employee
# rights. At stage 1 only the top-authority director assigns it (decision 22.10.2026).
BROAD_SETS = frozenset({COMPANY_ADMIN})
BROAD_PERMISSIONS = frozenset({"employee.rights.manage"})
