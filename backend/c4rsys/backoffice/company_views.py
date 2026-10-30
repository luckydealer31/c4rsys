"""What a company sees of the work of the c4rsys staff (ТЗ § 24.3.2): the journal of finished
privileged sessions — moment, unit, reason category and viewed sections, without the person of
the staff member and the content of the ticket. Seen by the top-authority director and the
employees with the security right."""

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET

from c4rsys.accounts.middleware import require_user
from c4rsys.backoffice import privileged
from c4rsys.companies import access
from c4rsys.companies import services as companies
from c4rsys.platform import errors


@require_GET
def staff_access(request: HttpRequest, cmp: str) -> JsonResponse:
    user, session = require_user(request)
    context = companies.resolve_company(user, cmp, session.id)
    actor = companies.company_actor(user, context, session.id)
    if not access.RightsResolver().has_permission(actor, "company.security"):
        raise errors.permission_denied("company.security")
    return JsonResponse({"entries": privileged.company_journal(context.company.id)})
