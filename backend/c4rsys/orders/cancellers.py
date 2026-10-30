"""Cancelling a position the client refused while it has not started (§ 12.3.1). Each module
cancels its own position; a started one stays with what was done."""

from uuid import UUID

from c4rsys.platform.commands import CommandContext


def service_job(ctx: CommandContext, company_id: UUID, item_id: UUID, reason: str) -> bool:
    from c4rsys.service import operations
    from c4rsys.service.models import ServiceJob

    job = ServiceJob.objects.select_for_update().filter(company_id=company_id, pk=item_id).first()
    if job is None or job.state != ServiceJob.State.PLANNED:
        return False
    operations.cancel(ctx, job, reason)
    return True


def extra_service(ctx: CommandContext, company_id: UUID, item_id: UUID, reason: str) -> bool:
    from c4rsys.rentals import extra_lines
    from c4rsys.rentals.models import ExtraServiceLine

    line = ExtraServiceLine.objects.filter(company_id=company_id, pk=item_id).first()
    if line is None or line.state != ExtraServiceLine.State.PLANNED:
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

    found = Delivery.objects.select_for_update().filter(company_id=company_id, pk=item_id).first()
    if found is None or found.state != Delivery.State.PLANNED:
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
