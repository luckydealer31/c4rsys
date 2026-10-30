"""Cancelling a position the client refused while it has not started (§ 12.3.1). Each module
cancels its own position; a started one stays with what was done. A position that cannot be
cancelled now returns False: the refusal is recorded all the same and the employee settles the
position by hand (it does not start after the refusal)."""

from uuid import UUID

from c4rsys.platform.commands import CommandContext


def service_job(ctx: CommandContext, company_id: UUID, item_id: UUID, reason: str) -> bool:
    from c4rsys.service import operations

    return operations.cancel_refused(ctx, company_id, item_id, reason)


def extra_service(ctx: CommandContext, company_id: UUID, item_id: UUID, reason: str) -> bool:
    from c4rsys.rentals import extra_lines
    from c4rsys.rentals.models import ExtraServiceLine

    line = ExtraServiceLine.objects.filter(company_id=company_id, pk=item_id).first()
    # A linked service follows its rental: after the issue it has started (decision У5,
    # § 13.4.15) and stays with what was done (§ 12.3.1).
    # TODO(spec): should a service added to an issued rental under the approval policy wait
    # (not be charged, not count as started) until the client answers?
    if line is None or extra_lines.display_state(line) != ExtraServiceLine.State.PLANNED:
        return False
    extra_lines.cancel(ctx, company_id, item_id, reason, line.version)
    return True


def sale(ctx: CommandContext, company_id: UUID, item_id: UUID, reason: str) -> bool:
    from c4rsys.warehouse import sales
    from c4rsys.warehouse.models import SaleLine

    line = SaleLine.objects.filter(company_id=company_id, pk=item_id).first()
    if line is None or line.state != SaleLine.State.RESERVED:
        return False
    sales.remove(ctx, company_id, item_id)
    return True


def rental(ctx: CommandContext, company_id: UUID, item_id: UUID, reason: str) -> bool:
    from c4rsys.rentals import bookings
    from c4rsys.rentals.models import Rental

    found = Rental.objects.filter(company_id=company_id, pk=item_id).first()
    if found is None or found.state != Rental.State.BOOKED:
        return False
    bookings.cancel(ctx, company_id, item_id, reason, None)
    return True


def delivery(ctx: CommandContext, company_id: UUID, item_id: UUID, reason: str) -> bool:
    from c4rsys.delivery import services
    from c4rsys.delivery.models import Delivery

    if not Delivery.objects.filter(company_id=company_id, pk=item_id).exists():
        return False
    found = services.locked(company_id, item_id, None)  # vehicle → delivery, as everywhere
    if found.state != Delivery.State.PLANNED:
        return False
    services.cancel(ctx, found, reason)
    return True


def register() -> None:
    from c4rsys.orders import approvals

    approvals.register_canceller("service_job", service_job)
    approvals.register_canceller("extra_service", extra_service)
    approvals.register_canceller("sale", sale)
    approvals.register_canceller("rental", rental)
    approvals.register_canceller("delivery", delivery)
