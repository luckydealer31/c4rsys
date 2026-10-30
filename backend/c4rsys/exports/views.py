"""HTTP API of the full export (ТЗ § 22.6.1, § 2.8.1; decision Б-6)."""

from datetime import UTC
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

from django.http import FileResponse, HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from c4rsys.accounts.middleware import require_user
from c4rsys.companies import access
from c4rsys.companies import services as companies
from c4rsys.companies.views import run_and_respond
from c4rsys.exports import services
from c4rsys.exports.models import DataExport
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult


def _actor(request: HttpRequest, cmp: str) -> tuple[companies.CompanyContext, Actor]:
    """The right for the whole company: the export holds the whole company (PROPOSAL)."""
    user, session = require_user(request)
    context = companies.resolve_company(user, cmp, session.id)
    actor = companies.company_actor(user, context, session.id)
    if not access.RightsResolver().has_permission(actor, services.PERMISSION, whole_company=True):
        raise errors.permission_denied(services.PERMISSION)
    return context, actor


@require_http_methods(["GET", "POST"])
def export_list(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        # Expired files are removed by the periodic check (`services.purge_expired`).
        now = timezone.now()
        items = [
            services.payload(e, now)
            for e in DataExport.objects.filter(company=context.company).order_by("-created_at")[:20]
        ]
        return JsonResponse({"exports": items})

    def handler(ctx: CommandContext) -> CommandResult:
        export = services.create(ctx, context.company, actor)
        return CommandResult(201, {"export": services.payload(export, timezone.now())})

    return run_and_respond(request, "data.export", actor, {}, handler)


@require_GET
def export_download(request: HttpRequest, cmp: str, export_id: UUID) -> FileResponse:
    """The link needs the sign-in and the current right and works 24 hours (§ 2.8.1)."""
    context, actor = _actor(request, cmp)
    export = services.get(context.company.id, export_id)
    if export is None:
        raise errors.not_found()
    path = services.path_of(export)
    if export.removed_at is not None or export.expires_at <= timezone.now() or not path.exists():
        raise errors.business_rule("EXPORT_EXPIRED")
    services.journal_download(export, actor)
    # The moment in the company's time zone, as the interface shows it.
    zone = ZoneInfo(context.company.timezone) if context.company.timezone else UTC
    moment = timezone.localtime(export.created_at, zone)
    name = f"c4rsys-{context.company.public_id}-{moment:%Y%m%d-%H%M}.zip"
    response = FileResponse(Path(path).open("rb"), content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    response["Cache-Control"] = "private, no-store"
    return response
