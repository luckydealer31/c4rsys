"""A rental and its order (ТЗ § 12.1.7, § 12.2.1–12.2.2, § 12.3.7; customer's decisions З2, З4–З6
of 28.10.2026).

- A preliminary order offers rentals: a vehicle, a tariff and a planned period with its cost. A
  proposal holds nothing; «Form the order» turns it into a booking of the same row, checking the
  vehicle is free. The tariff stays as it was offered: the snapshot is taken with the proposal.
- The whole order is cancelled with its proposals, bookings and services not started; an issued
  or returned rental keeps it from cancelling: it is transferred or annulled first.
- A wrong rental is annulled with a reason: its charges and those of its linked services go to
  zero; where the vehicle is stays as it is (corrected apart).
- A right rental in a wrong order moves to another open order of the same client or to a new one
  with its linked services and charges; payments stay where they are and are relinked in finance.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from c4rsys.companies import access
from c4rsys.finance import charges
from c4rsys.finance.models import ChargeEntry
from c4rsys.fleet import services as fleet
from c4rsys.orders import services as orders
from c4rsys.orders.models import Order
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.platform.counters import next_number
from c4rsys.rentals import billing, bookings, changes, extra_lines
from c4rsys.rentals.models import Rental, RentalManualCharge

BOOK = "rental.book"
ANNUL = "rental.annul"
TRANSFER = "rental.transfer"

PROPOSED = Rental.State.PROPOSED
ISSUED = (Rental.State.ACTIVE, Rental.State.RETURNED)


# --- Proposals of a preliminary order ----------------------------------------------------------


@dataclass(frozen=True)
class ProposalData:
    vehicle_card_id: UUID
    tariff_id: UUID
    planned_start: datetime
    planned_end: datetime
    issue_location_id: UUID | None = None
    planned_return_location_id: UUID | None = None


def proposals_of(company_id: UUID, order_id: UUID) -> list[Rental]:
    return list(
        Rental.objects.filter(company_id=company_id, order_id=order_id, state=PROPOSED).order_by(
            "created_at"
        )
    )


def add_proposal(
    ctx: CommandContext, company_id: UUID, order_id: UUID, data: ProposalData
) -> Rental:
    """A rental offered in a preliminary order of a client (decision З2)."""
    if data.planned_end <= data.planned_start:
        raise errors.validation_error("planned_end", code="PERIOD_INVALID")
    order = orders.open_order_of_client(company_id, order_id, preliminary_ok=True)
    if order.state != Order.State.PRELIMINARY:
        raise errors.business_rule("ORDER_NOT_PRELIMINARY", field="order_id")
    card = fleet.lock_card(company_id, data.vehicle_card_id)
    tariff, snap = bookings._tariff(card, data.tariff_id)
    location, zone, place = bookings._place(
        company_id,
        bookings.BookingData(
            vehicle_card_id=card.id,
            planned_start=data.planned_start,
            planned_end=data.planned_end,
            issue_location_id=data.issue_location_id,
        ),
    )
    if data.planned_return_location_id is not None:
        from c4rsys.companies import locations

        locations.require_usable(
            company_id, data.planned_return_location_id, "planned_return_location_id"
        )
    rental = Rental.objects.create(
        company_id=company_id,
        order=order,
        vehicle_card=card,
        client_id=order.client_id,
        tariff=tariff,
        tariff_snapshot=snap,
        calc_timezone=zone,
        issue_location_id=location.id if location else None,
        issue_place_kind=place.kind if place else "",
        planned_start=data.planned_start,
        planned_end=data.planned_end,
        planned_return_location_id=data.planned_return_location_id,
        state=PROPOSED,
        created_by_user_id=ctx.actor.user_id,
        created_at=timezone.now(),
    )
    ctx.audit(
        "rental.proposed",
        object_type="rental",
        object_id=rental.id,
        after={**bookings.snapshot(rental), "order": str(order.id), "order_number": order.number},
    )
    return rental


def _locked_proposal(company_id: UUID, rental_id: UUID, expected_version: int) -> Rental:
    rental = Rental.objects.select_for_update().filter(company_id=company_id, pk=rental_id).first()
    if rental is None:
        raise errors.not_found()
    if rental.version != expected_version:
        raise errors.version_conflict()
    if rental.state != PROPOSED:
        raise errors.business_rule("RENTAL_NOT_PROPOSED", state=rental.state)
    orders.ensure_editable(company_id, rental.order_id)
    return rental


def _withdraw(ctx: CommandContext, rental: Rental, reason: str) -> None:
    rental.state = Rental.State.CANCELLED
    rental.cancelled_at = timezone.now()
    rental.cancel_reason = reason
    rental.version += 1
    rental.save()
    ctx.audit(
        "rental.proposal_withdrawn",
        object_type="rental",
        object_id=rental.id,
        before={"state": PROPOSED},
        after={"state": rental.state},
        reason=reason,
    )


def withdraw_proposal(
    ctx: CommandContext, company_id: UUID, rental_id: UUID, expected_version: int
) -> Rental:
    """The proposal leaves the preliminary order; the row and its history stay."""
    rental = _locked_proposal(company_id, rental_id, expected_version)
    _withdraw(ctx, rental, "")
    return rental


def on_order_formed(ctx: CommandContext, company_id: UUID, order_id: UUID) -> None:
    """Handler for orders: the proposals become bookings with «Book» in their location, the
    vehicle must be free (§ 12.2.2); then the order's cost is charged."""
    resolver = access.RightsResolver()
    for found in proposals_of(company_id, order_id):
        if not resolver.has_permission(ctx.actor, BOOK, location_id=found.issue_location_id):
            raise errors.permission_denied(BOOK)
        card = fleet.lock_card(company_id, found.vehicle_card_id)  # vehicle → rental
        rental = Rental.objects.select_for_update().get(pk=found.pk)
        start, end = rental.planned_start, rental.planned_end
        if start is None or end is None:
            raise RuntimeError("A proposal has its planned period")
        bookings._require_free(card.id, start, end, exclude=rental.id)
        rule = bookings.policy_for(company_id, rental.issue_location_id)
        rental.state = Rental.State.BOOKED
        rental.booking_period = (start, end)
        rental.booking_number = next_number(company_id, "booking")
        rental.auto_cancel_rule = rule
        rental.auto_cancel_at = bookings._auto_cancel_at(rule, start)
        rental.version += 1
        try:
            with transaction.atomic():
                rental.save()
        except IntegrityError as exc:
            raise errors.business_rule("VEHICLE_NOT_AVAILABLE") from exc
        ctx.audit(
            "rental.booked",
            object_type="rental",
            object_id=rental.id,
            before={"state": PROPOSED},
            after={**bookings.snapshot(rental), "order": str(order_id), "from_proposal": True},
        )
    billing.on_client_assigned(ctx, company_id, order_id)


# --- Cancellation of the whole order -------------------------------------------------------------


def on_order_cancel(ctx: CommandContext, company_id: UUID, order_id: UUID, reason: str) -> None:
    """Handler for orders: proposals and bookings are cancelled, then the services not started; an
    issued or returned rental refuses (§ 12.2.1, § 12.3.7)."""
    for rental in Rental.objects.select_for_update().filter(
        company_id=company_id, order_id=order_id
    ):
        if rental.state == PROPOSED:
            _withdraw(ctx, rental, reason)
        elif rental.state == Rental.State.BOOKED:
            bookings.cancel(ctx, company_id, rental.id, reason, None)
        elif rental.state in ISSUED:
            raise errors.business_rule("ORDER_HAS_ACTIVE_RENTAL", rental_id=str(rental.id))
    extra_lines.cancel_for_order(ctx, company_id, order_id, reason)


# --- Annulment of a wrong rental ------------------------------------------------------------------


def _zero(ctx: CommandContext, rental: Rental, source_type: str, source_id: UUID) -> None:
    currency = charges.charged_currency(source_type, source_id)
    if currency is None or rental.client_id is None:
        return
    charges.set_charge(
        ctx,
        company_id=rental.company_id,
        order_id=rental.order_id,
        client_id=rental.client_id,
        source_type=source_type,
        source_id=source_id,
        amount=Decimal(0),
        currency=currency,
        reason="annulled",
        calculation={"kind": "annulled", "rental": str(rental.id)},
    )


def annul(
    ctx: CommandContext, company_id: UUID, rental_id: UUID, reason: str, expected_version: int
) -> Rental:
    """An issued or returned rental entered by mistake (§ 12.3.7; decision З5). A booking is
    cancelled instead. The rental, its inspections and its history stay."""
    reason = reason.strip()
    if not reason:
        raise errors.validation_error("reason", code="FIELD_REQUIRED")
    rental = changes._locked(company_id, rental_id, expected_version)
    if rental.state not in ISSUED:
        raise errors.business_rule("RENTAL_NOT_ISSUED", state=rental.state)
    before = {
        "state": rental.state,
        "formalization_completed_at": rental.formalization_completed_at,
    }
    now = timezone.now()
    rental.state = Rental.State.ANNULLED
    rental.cancelled_at = now
    rental.cancel_reason = reason
    rental.version += 1
    rental.save()
    for item in RentalManualCharge.objects.select_for_update().filter(
        rental_id=rental.id, cancelled_at__isnull=True
    ):
        item.cancelled_at = now
        item.cancelled_by_user_id = ctx.actor.user_id
        item.cancel_reason = reason
        item.save(update_fields=["cancelled_at", "cancelled_by_user_id", "cancel_reason"])
        _zero(ctx, rental, ChargeEntry.SourceType.RENTAL_MANUAL, item.id)
    _zero(ctx, rental, ChargeEntry.SourceType.RENTAL_MILEAGE, rental.id)
    billing.charge_base(ctx, rental, "annulled")
    extra_lines.annul_linked(ctx, rental, reason)
    ctx.audit(
        "rental.annulled",
        object_type="rental",
        object_id=rental.id,
        before=before,
        after={"state": rental.state},
        reason=reason,
    )
    return rental


# --- Transfer to another order --------------------------------------------------------------------


def transfer(
    ctx: CommandContext,
    company_id: UUID,
    rental_id: UUID,
    target_order_id: UUID | None,
    expected_version: int,
) -> Rental:
    """To another open order of the same client, or to a new one (None). The rental keeps its id,
    vehicle, dates, actions and history; its charges and linked services go with it (§ 12.3.7;
    decision З6). A closed order takes no part."""
    rental = changes._locked(company_id, rental_id, expected_version)
    if rental.state not in (Rental.State.BOOKED, *ISSUED):
        raise errors.business_rule("RENTAL_TRANSFER_NOT_ALLOWED", state=rental.state)
    source = orders.lock_open_order(company_id, rental.order_id)
    if target_order_id == source.id:
        raise errors.business_rule("TRANSFER_SAME_ORDER", field="order_id")
    if target_order_id is not None:
        target = orders.lock_open_order(company_id, target_order_id)
        if target.is_internal or target.client_id != source.client_id:
            raise errors.business_rule("ORDER_CLIENT_MISMATCH", field="order_id")
    else:
        target = orders.create_order(
            ctx,
            company_id=company_id,
            client_id=source.client_id,
            responsible_employment_id=ctx.actor.employment_id,
        )
    rental.order_id = target.id
    rental.version += 1
    rental.save()
    sources: list[tuple[str, UUID]] = [
        (str(ChargeEntry.SourceType.RENTAL_BASE), rental.id),
        (str(ChargeEntry.SourceType.RENTAL_MILEAGE), rental.id),
    ] + [
        (str(ChargeEntry.SourceType.RENTAL_MANUAL), item_id)
        for item_id in RentalManualCharge.objects.filter(rental_id=rental.id).values_list(
            "id", flat=True
        )
    ]
    # Damages charged in the rental go with it (§ 10.5.3).
    from c4rsys.inspections import damages

    sources += damages.charge_sources(rental.id)
    for source_type, source_id in sources:
        charges.move_source(
            ctx,
            source_type=source_type,
            source_id=source_id,
            order_id=target.id,
            reason="transferred",
        )
    extra_lines.move_linked(ctx, rental, target.id)
    from c4rsys.orders import approvals

    approvals.move(ctx, company_id, "rental", rental.id, target.id)  # § 12.3.7
    moved: dict[str, Any] = {"rental": str(rental.id)}
    ctx.audit(
        "rental.transferred",
        object_type="rental",
        object_id=rental.id,
        before={"order": str(source.id), "order_number": source.number},
        after={"order": str(target.id), "order_number": target.number},
    )
    ctx.audit(
        "order.rental_moved_out",
        object_type="order",
        object_id=source.id,
        after={**moved, "to_order": str(target.id), "to_number": target.number},
    )
    ctx.audit(
        "order.rental_moved_in",
        object_type="order",
        object_id=target.id,
        after={**moved, "from_order": str(source.id), "from_number": source.number},
    )
    return rental
