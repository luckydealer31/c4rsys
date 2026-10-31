"""The history of a client in the card of the company (ТЗ § 6.1.14, § 8.1.2, § 6.5.2) and the
history a customer company sees through the card of its contractor (§ 6.1.14).

* The card of a client shows its orders with their rentals, service and sales, the payments, and
  the vehicles of its orders — of the card and of the cards merged into it. Each part follows the
  rights of its section: orders by «view orders» in their locations, vehicles by «view vehicles»;
  the payments are shown to those who may find the client, as its balance is (PROPOSAL).
* A customer company sees, in the card of a counterparty linked with the contractor's profile,
  what the contractor shares with a linked client: its orders as the client sees them, without
  the contractor's internal data (decision Б-1). After a break only what was shared before stays;
  a revoked access shows nothing (§ 6.6.3, § 6.5.5).
"""

from collections.abc import Callable
from typing import Any
from uuid import UUID

from c4rsys.parties import links, services
from c4rsys.parties.models import Counterparty, ProfileLink
from c4rsys.platform.commands import Actor
from c4rsys.platform.models import Currency
from c4rsys.platform.money import round_money

HISTORY_LIMIT = 100

# (company_id, order_ids) -> (order_id, vehicle card id, source) of the operations of the orders.
OrderVehicles = Callable[[UUID, list[UUID]], list[tuple[UUID, UUID, str]]]
# (company_id, client_ids) -> vehicle card ids declared as owned by the clients.
OwnedVehicles = Callable[[UUID, list[UUID]], list[UUID]]
_order_vehicles: list[OrderVehicles] = []
_owned: list[OwnedVehicles] = []


def register_order_vehicles(provider: OrderVehicles) -> None:
    _order_vehicles.append(provider)


def register_owned_vehicles(provider: OwnedVehicles) -> None:
    _owned.append(provider)


def _money(places: dict[str, int], currency: str, value: Any) -> str:
    """An amount rounded to the minor units of its currency."""
    if currency not in places:
        found = Currency.objects.filter(code=currency).values_list("minor_units", flat=True)
        places[currency] = found.first() or 2
    return str(round_money(value, places[currency]))


def _has(actor: Actor, permission: str) -> bool:
    from c4rsys.companies import access

    return access.RightsResolver().has_permission(actor, permission)


def client_history(actor: Actor, company_id: UUID, client_id: UUID) -> dict[str, Any]:
    from c4rsys.finance import settlements
    from c4rsys.fleet import services as fleet
    from c4rsys.orders import services as orders

    card = services.get_client(company_id, client_id)
    family = services.family_ids(company_id, card.id)
    visible = [
        o
        for o in orders.client_orders(company_id, family)[:HISTORY_LIMIT]
        if orders.can_view(actor, o)
    ]
    order_ids = [o.id for o in visible]
    vehicles_of: dict[UUID, set[UUID]] = {}
    sources: dict[UUID, set[str]] = {}
    for provider in _order_vehicles:
        for order_id, vehicle_id, source in provider(company_id, order_ids):
            vehicles_of.setdefault(order_id, set()).add(vehicle_id)
            sources.setdefault(vehicle_id, set()).add(source)
    for owned in _owned:
        for vehicle_id in owned(company_id, family):
            sources.setdefault(vehicle_id, set()).add("owner")
    show_vehicles = _has(actor, "vehicle.view")
    names = fleet.card_names(company_id, set(sources)) if show_vehicles else {}

    places: dict[str, int] = {}

    def money(currency: str, value: Any) -> str:
        return _money(places, currency, value)

    def amounts(order: Any) -> dict[str, dict[str, str]]:
        return {
            currency: {
                "charged": money(currency, m.charged),
                "paid": money(currency, m.paid),
                "balance": money(currency, m.balance),
            }
            for currency, m in sorted(orders.amounts_of(order).items())
        }

    result: dict[str, Any] = {
        "cards": [str(i) for i in family],
        "orders": [
            {
                "id": str(o.id),
                "number": o.number,
                "state": o.state,
                "client_id": str(o.client_id),
                "created_at": o.created_at.isoformat(),
                "closed_at": o.closed_at.isoformat() if o.closed_at else None,
                "operations": [
                    {"type": op.type, "id": str(op.id), "state": op.state}
                    for op in orders.operations_of(o)
                ],
                "amounts": amounts(o),
                "vehicles": sorted(str(v) for v in vehicles_of.get(o.id, set()))
                if show_vehicles
                else [],
            }
            for o in visible
        ],
        "vehicles": [
            {"id": str(v), "name": names.get(v, ""), "sources": sorted(sources[v])}
            for v in sorted(names, key=lambda x: names[x])
        ],
        "payments": [],
    }
    if _has(actor, "client.find") or _has(actor, "client.list"):
        payments = []
        for member in family:
            for m in settlements.movements_of_client(company_id, member).order_by("-created_at")[
                :HISTORY_LIMIT
            ]:
                payments.append(
                    {
                        "id": str(m.id),
                        "number": m.number,
                        "kind": m.kind,
                        "direction": m.direction,
                        "state": m.state,
                        "amount": money(m.currency_id, m.amount),
                        "currency": m.currency_id,
                        "client_id": str(member),
                        "created_at": m.created_at.isoformat(),
                    }
                )
        result["payments"] = sorted(payments, key=lambda p: p["created_at"], reverse=True)
    return result


def contractor_history(company_id: UUID, counterparty: Counterparty) -> list[dict[str, Any]]:
    """The orders the contractor made for this company as its client: its client card linked
    with this company's profile, through the counterparty card linked with the contractor."""
    from c4rsys.orders import services as orders

    contractor = counterparty.linked_company_id
    if contractor is None:
        return []
    places: dict[str, int] = {}
    found = []
    for link in ProfileLink.objects.filter(
        company_id=contractor,
        profile=ProfileLink.Profile.COMPANY,
        target_company_id=company_id,
        client__isnull=False,
        state__in=(ProfileLink.State.ACCEPTED, ProfileLink.State.BROKEN),
    ).order_by("-decided_at"):
        cards = links.history_cards(link)
        if not cards:
            continue
        for order in orders.client_orders(contractor, cards, link.broken_at)[:HISTORY_LIMIT]:
            view = orders.client_view(order)
            view["amounts"] = {
                currency: {
                    "charged": _money(places, currency, m.charged),
                    "paid": _money(places, currency, m.paid),
                }
                for currency, m in sorted(view["amounts"].items())
            }
            view["lines"] = [
                {**line, "amount": _money(places, line["currency"], line["amount"])}
                for line in view["lines"]
            ]
            view["cost_changes"] = [
                {**c, "difference": _money(places, c["currency"], c["difference"])}
                for c in view["cost_changes"]
            ]
            found.append(view)
    return found
