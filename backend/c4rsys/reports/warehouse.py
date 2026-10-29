"""The ready dashboard «Warehouse» (ТЗ § 21.6.1) as blocks of a report, for the XLSX export
(§ 21.2.9). The figures are those of `warehouse.dashboard`, over the warehouses the employee may
see; nothing is counted here again."""

from typing import Any

from c4rsys.companies import locations
from c4rsys.companies.models import Company
from c4rsys.platform.commands import Actor
from c4rsys.reports.overview import Block
from c4rsys.reports.scope import Area, Filters


def _flat(value: Any) -> Any:
    if isinstance(value, dict):
        if "amount" in value:
            return value["amount"] + (" *" if value.get("incomplete") else "")
        return value.get("name") or ", ".join(f"{k}: {_flat(v)}" for k, v in value.items())
    if isinstance(value, list):
        return ", ".join(str(_flat(v)) for v in value)
    return value


def _rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: _flat(v) for k, v in item.items()} for item in items]


def build(actor: Actor, company: Company, filters: Filters) -> list[Block]:
    from c4rsys.companies import access
    from c4rsys.warehouse import dashboard
    from c4rsys.warehouse.stock_views import visible_warehouses

    if not access.RightsResolver().has_permission(actor, "warehouse.view"):
        return []
    visible = visible_warehouses(company.id, actor)
    if not visible:
        return []
    all_count = locations.storage_contexts(company.id).count()
    data = dashboard.build(
        company, visible, all_count, dashboard.Filters(date_from=filters.start, date_to=filters.end)
    )
    whole = not data["scope"]["partial"]
    where = Area(whole, frozenset())
    names = data["scope"]["warehouses"]
    value = data["value"]
    period = [
        {"operation": k, **{x: _flat(y) for x, y in v.items()}} for k, v in data["period"].items()
    ]
    blocks = [
        Block("warehouse.stock", "warehouse", "table", None, _rows(data["stock"]), where),
        Block(
            "warehouse.value",
            "warehouse",
            "table",
            None,
            [
                {"kind": "cost", "amount": _flat(value["cost"]), "currency": data["currency"]},
                {"kind": "sale", "amount": _flat(value["sale"]), "currency": data["currency"]},
            ],
            where,
        ),
        Block("warehouse.issued", "warehouse", "table", None, _rows(data["issued"]), where),
        Block("warehouse.in_transit", "warehouse", "table", None, _rows(data["in_transit"]), where),
        Block(
            "warehouse.below_minimum",
            "warehouse",
            "table",
            None,
            _rows(data["below_minimum"]),
            where,
        ),
        Block("warehouse.expiring", "warehouse", "table", None, _rows(data["expiring"]), where),
        Block("warehouse.period", "warehouse", "table", None, period, where),
        Block("warehouse.purchases", "warehouse", "table", None, _rows(data["purchases"]), where),
    ]
    if not whole:
        # § 21.1.1: with partial access the warehouses of the calculation are named.
        for b in blocks:
            b.warehouses = names
    return blocks
