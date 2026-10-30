from dataclasses import dataclass
from typing import Any

from django.test import Client

from c4rsys.accounts.models import UserAccount
from c4rsys.backoffice.models import SystemRole, SystemRoleAssignment, SystemStaffMember
from tests.accounts.helpers import login, post
from tests.companies.helpers import key, named_user

STAFF = "staff.localhost"


@dataclass
class Member:
    client: Client
    public_id: str
    member_id: str


def staff_client() -> Client:
    return Client(HTTP_HOST=STAFF)


def member(email: str, roles: list[str], unit: str = "") -> Member:
    """A registered user made a system staff member with `roles`, signed in on the staff host."""
    personal = Client()
    named_user(personal, email)
    user = UserAccount.objects.get(contacts__value=email)
    found = SystemStaffMember.objects.create(user=user, unit=unit)
    for code in roles:
        SystemRoleAssignment.objects.create(member=found, role=SystemRole.objects.get(code=code))
    client = staff_client()
    response = login(client, email)
    assert response.status_code == 200, response.content
    return Member(client, user.public_id, str(found.id))


def custom_role(code: str, permissions: list[str]) -> SystemRole:
    return SystemRole.objects.create(code=code, name=code, permissions=permissions)


def grant(m: Member, code: str) -> None:
    SystemRoleAssignment.objects.create(
        member_id=m.member_id, role=SystemRole.objects.get(code=code)
    )


def sp(m: Member, path: str, body: dict[str, Any] | None = None) -> Any:
    """A command of the internal API with a fresh Idempotency-Key."""
    return post(m.client, f"/api/staff/{path}", body or {}, key())


def err(response: Any) -> str:
    code: str = response.json()["error"]["code"]
    return code


def open_session(m: Member, cmp: str, scopes: list[str] | None = None, **extra: Any) -> Any:
    body = {
        "target_type": "company",
        "target": cmp,
        "reason_category": "support_ticket",
        "reason": "The client asks why the booking was cancelled",
        "scopes": scopes or [],
        **extra,
    }
    return sp(m, "privileged", body)
