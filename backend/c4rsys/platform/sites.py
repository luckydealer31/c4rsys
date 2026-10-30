"""Which interface an address serves (customer's decisions of 30.10.2026 and В-11.1).

* The personal cabinet has its own domain (`C4R_CABINET_HOSTS`).
* The internal interface of the system staff has its own subdomain (`C4R_STAFF_HOSTS`) with its
  own sign-in; its API answers nowhere else.
* Every other allowed host serves the B2B interface.
"""

from django.conf import settings
from django.http import HttpRequest
from django.http.request import split_domain_port

STAFF = "staff"
CABINET = "cabinet"
COMPANY = ""


def host_of(request: HttpRequest) -> str:
    """The host as Django validates it (ALLOWED_HOSTS), lowercase, without the port."""
    domain, _port = split_domain_port(request.get_host())
    return domain


def site_of_host(host: str) -> str:
    if host in settings.C4R_STAFF_HOSTS:
        return STAFF
    if host in settings.C4R_CABINET_HOSTS:
        return CABINET
    return COMPANY


def site_of(request: HttpRequest) -> str:
    return site_of_host(host_of(request))


def is_staff_host(request: HttpRequest) -> bool:
    return site_of(request) == STAFF
