"""The public request to recover access through the support, on the ordinary domain (ТЗ § 2.3.1;
decision В-11.2: switched off by the platform setting until the regulation is ready).

Like the other sign-in steps the applicant has no session: the steps are anonymous, rate-limited
by the code limits of § 2.4.4 and journaled. The answers never tell whether the account exists.
"""

from django.db import transaction
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET, require_POST

from c4rsys.accounts import services as accounts
from c4rsys.backoffice import recovery
from c4rsys.backoffice.blocking import support_contact
from c4rsys.platform import errors, sites
from c4rsys.platform.http import client_ip, get_str, parse_json, user_agent


def _client(request: HttpRequest) -> accounts.Client:
    return accounts.Client(
        ip=client_ip(request), user_agent=user_agent(request), host=sites.host_of(request)
    )


@require_GET
def status(request: HttpRequest) -> JsonResponse:
    """Whether the recovery through the support is available, and the support contact."""
    return JsonResponse({"enabled": recovery.enabled(), "support": support_contact()})


@require_POST
def email_start(request: HttpRequest) -> JsonResponse:
    """A code to the new email the applicant wants instead of the unavailable one."""
    recovery.require_enabled()
    data = parse_json(request)
    started = accounts.recovery_email_start(get_str(data, "email"), _client(request))
    return JsonResponse(started.as_dict())


@require_POST
def email_verify(request: HttpRequest) -> JsonResponse:
    recovery.require_enabled()
    data = parse_json(request)
    accounts.recovery_email_verify(get_str(data, "flow"), get_str(data, "code", max_length=10))
    return JsonResponse({"status": "code_confirmed"})


@require_POST
def submit(request: HttpRequest) -> JsonResponse:
    """The request: a c4rsys ID or username, a known contact, optionally the confirmed new email
    (its flow) and a free description. The answer is the number of the request only."""
    recovery.require_enabled()
    data = parse_json(request)
    identifier = get_str(data, "identifier", max_length=100)
    known_contact = get_str(data, "known_contact")
    description = get_str(data, "description", max_length=4000, required=False)
    flow = get_str(data, "flow", max_length=200, required=False)
    with transaction.atomic():
        new_email = accounts.recovery_email_take(flow) if flow else ""
        case = recovery.submit(
            identifier=identifier,
            known_contact=known_contact,
            new_email=new_email,
            description=description,
            ip=client_ip(request),
        )
    return JsonResponse({"number": case.number, "support": support_contact()}, status=201)


@require_GET
def password_link(request: HttpRequest, token: str) -> JsonResponse:
    recovery.require_enabled()
    if not accounts.recovery_link_valid(token):
        raise errors.business_rule("FLOW_EXPIRED")
    return JsonResponse({"valid": True})


@require_POST
def password_set(request: HttpRequest) -> JsonResponse:
    """The new cloud password by the personal link; a new backup code is shown once."""
    recovery.require_enabled()
    data = parse_json(request)
    backup_code = accounts.recovery_set_password(
        get_str(data, "token", max_length=200), get_str(data, "password", max_length=200)
    )
    return JsonResponse({"backup_code": backup_code})
