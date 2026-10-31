"""What rentals give the vehicle card (ТЗ § 8.2.10, § 8.3.15, § 8.7.5, § 8.8.7): unfinished
rentals and bookings, the mileage terms in the odometer unit, the operation periods passed to the
owner."""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db.models import Q

from c4rsys.fleet.odometer import Conversion
from c4rsys.rentals.models import Rental, TariffVehicleTerms

State = Rental.State
INCLUDED_PLACES = Decimal("0.1")
PRICE_PLACES = Decimal("0.0001")


def pending(company_id: UUID, card_id: UUID, now: datetime) -> list[dict[str, Any]]:
    """Bookings, running rentals and returned ones whose formalization is not completed."""
    found = Rental.objects.filter(company_id=company_id, vehicle_card_id=card_id).filter(
        Q(state__in=(State.BOOKED, State.ACTIVE))
        | Q(state=State.RETURNED, formalization_completed_at__isnull=True)
    )
    return [
        {
            "kind": "booking" if r.state == State.BOOKED else "rental",
            "id": str(r.id),
            "label": str(r.booking_number or ""),
            "active": r.state == State.ACTIVE,
            "order_id": str(r.order_id) if r.order_id else None,
        }
        for r in found
    ]


def _mileage(terms: dict[str, Any], conv: Conversion) -> dict[str, Any]:
    result = dict(terms)
    if result.get("included_per_unit") not in (None, ""):
        result["included_per_unit"] = str(
            conv.distance(Decimal(result["included_per_unit"]), INCLUDED_PLACES)
        )
    if result.get("overage_price") not in (None, ""):
        result["overage_price"] = str(conv.price(Decimal(result["overage_price"]), PRICE_PLACES))
    return result


def unit_values(
    company_id: UUID, card_id: UUID, conv: Conversion, apply: bool
) -> list[dict[str, Any]]:
    """§ 8.2.10: the included mileage converts with the distances, the price of a unit by the
    inverse factor, so one change does not change the money. The tariff terms of the vehicle and
    the snapshots of rentals not formalized yet change; completed rentals keep theirs."""
    changes: list[dict[str, Any]] = []
    terms_rows = TariffVehicleTerms.objects.filter(company_id=company_id, vehicle_card_id=card_id)
    rentals = Rental.objects.filter(
        company_id=company_id, vehicle_card_id=card_id, formalization_completed_at__isnull=True
    ).exclude(state__in=(State.CANCELLED, State.ANNULLED))
    if apply:
        # The preview reads only; the change locks what it rewrites.
        terms_rows, rentals = terms_rows.select_for_update(), rentals.select_for_update()
    for t in terms_rows:
        included = (
            conv.distance(t.included_per_unit, INCLUDED_PLACES)
            if t.included_per_unit is not None
            else None
        )
        price = conv.price(t.overage_price, PRICE_PLACES) if t.overage_price is not None else None
        changes.append(
            {
                "what": "tariff_terms",
                "id": str(t.id),
                "field": "mileage",
                "before": f"{t.included_per_unit}/{t.overage_price} {t.mileage_unit}",
                "after": f"{included}/{price} {conv.to_unit}",
            }
        )
        if apply:
            t.included_per_unit, t.overage_price, t.mileage_unit = included, price, conv.to_unit
            t.version += 1
            t.save(update_fields=["included_per_unit", "overage_price", "mileage_unit", "version"])
    for r in rentals:
        snapshot = dict(r.tariff_snapshot or {})
        terms = dict(snapshot.get("vehicle_terms") or {})
        if not terms:
            continue
        before = dict(terms)
        terms["mileage"] = _mileage(dict(terms.get("mileage") or {}), conv)
        terms["mileage_unit"] = conv.to_unit
        changes.append(
            {
                "what": "rental",
                "id": str(r.id),
                "field": "tariff_snapshot.vehicle_terms",
                "before": before,
                "after": terms,
            }
        )
        if apply:
            snapshot["vehicle_terms"] = terms
            Rental.objects.filter(pk=r.pk).update(tariff_snapshot=snapshot)
    return changes


def periods_for_owner(company_id: UUID, card_id: UUID, until: datetime | None) -> list[Any]:
    """The category «operation periods and load» (§ 8.3.15): when the vehicle was rented out,
    without the clients."""
    query = Rental.objects.filter(
        company_id=company_id,
        vehicle_card_id=card_id,
        state__in=(State.ACTIVE, State.RETURNED),
        actual_start__isnull=False,
    )
    if until is not None:
        query = query.filter(actual_start__lte=until)
    return [
        {
            "at": r.actual_start,
            "kind": "rental",
            "start": r.actual_start,
            "end": r.actual_end
            if until is None or (r.actual_end and r.actual_end <= until)
            else None,
        }
        for r in query.order_by("-actual_start")
    ]
