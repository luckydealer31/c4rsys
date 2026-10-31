"""Journals and masked contacts on the company side (ТЗ § 24.4.3, § 24.4.5)."""

from typing import Any

import pytest
from django.test import Client

from c4rsys.platform.models import AuditEvent
from tests.accounts.helpers import post
from tests.companies.helpers import create_company, key, named_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def cmp(client: Client) -> str:
    named_user(client)
    public_id: str = create_company(client).json()["company"]["public_id"]
    return public_id


def _hire_sharing(owner: Client, cmp: str, email: str, rights: list[dict[str, Any]]) -> Any:
    employee = Client()
    named_user(employee, email)
    usr = employee.get("/api/me").json()["user"]["public_id"]
    body = {"target": usr, "position_name": "Staff", "rights": rights}
    invitation = post(owner, f"/api/c/{cmp}/employees/invitations", body, key()).json()
    accepted = post(
        employee,
        f"/api/invitations/{invitation['invitation']['id']}/accept",
        {"share_email": True},
        key(),
    )
    assert accepted.status_code == 200, accepted.content
    return employee, invitation["invitation"]["employment_id"]


def _grant(permission: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "grant",
            "permission": permission,
            "locations": "all",
            "contexts": "all",
            "cash_accounts": "all",
        }
    ]


def test_personal_contacts_are_masked_in_the_list_of_employees(client: Client, cmp: str) -> None:
    _, employment = _hire_sharing(client, cmp, "kate@example.com", [])
    listed = client.get(f"/api/c/{cmp}/employees").json()["employees"]
    person = next(e for e in listed if e["id"] == employment)["user"]
    assert person["email"] == "k***@example.com"
    assert person["contacts_masked"] is True
    card = client.get(f"/api/c/{cmp}/employees/{employment}").json()["employee"]["user"]
    assert card["email"] == "kate@example.com"


def test_export_of_a_journal_needs_its_own_right(client: Client, cmp: str) -> None:
    viewer, viewer_employment = _hire_sharing(
        client, cmp, "viewer@example.com", _grant("employee.journal.view")
    )
    url = f"/api/c/{cmp}/employees/{viewer_employment}/journal"
    assert viewer.get(url).status_code == 200
    refused = viewer.get(f"{url}/export")
    assert refused.status_code == 403
    exported = client.get(f"{url}/export")
    assert exported.status_code == 200
    assert exported["Content-Type"].startswith("text/csv")
    assert exported.content.decode().startswith("occurred_at,actor,action")
    event = AuditEvent.objects.get(action="employee.journal_exported")
    assert event.object_id == viewer_employment
