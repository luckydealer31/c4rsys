"""Block 16: the platform administrator connects the modules of a company (ТЗ § 4.1.9); the
archive of the last tariff or service work after a warning (§ 4.3.2)."""

from typing import Any

import pytest
from django.test import Client

from c4rsys.backoffice.models import SystemJournalEntry
from c4rsys.platform.models import AuditEvent
from tests.accounts.helpers import post
from tests.backoffice.helpers import Member, err, member
from tests.companies.helpers import key
from tests.rentals.scenario import Scenario, add_location

pytestmark = pytest.mark.django_db


def _modules(m: Member, cmp: str, modules: list[str], reason: str = "Contract") -> Any:
    return m.client.put(
        f"/api/staff/companies/{cmp}/modules",
        data={"modules": modules, "reason": reason},
        content_type="application/json",
        headers=key(),
    )


def test_modules_are_connected_by_the_platform_administrator(
    owner: Client, s: Scenario, admin: Member, support: Member
) -> None:
    company = owner.get(f"/api/c/{s.cmp}").json()["company"]
    # A company starts with the modules of the first launch (PROPOSAL).
    assert company["modules"] == ["rental", "service", "warehouse"]
    # Only with the permission of the platform administrator.
    assert _modules(support, s.cmp, ["rental"]).status_code == 403
    # A module with enabled contexts is not disconnected.
    assert err(_modules(admin, s.cmp, ["service", "warehouse"])) == "MODULE_HAS_CONTEXTS"
    changed = _modules(admin, s.cmp, ["rental", "warehouse"])
    assert changed.status_code == 200, changed.content
    assert changed.json()["modules"] == ["rental", "warehouse"]
    assert owner.get(f"/api/c/{s.cmp}").json()["company"]["modules"] == ["rental", "warehouse"]
    entry = SystemJournalEntry.objects.get(action="company.modules_changed")
    assert entry.reason == "Contract" and entry.after == {"modules": ["rental", "warehouse"]}
    assert AuditEvent.objects.filter(action="company.modules_changed").exists()

    # A context of a module not connected cannot be added.
    refused = post(
        owner, f"/api/c/{s.cmp}/locations/{s.location}/contexts", {"type": "service"}, key()
    )
    assert refused.json()["error"]["code"] == "MODULE_NOT_CONNECTED"
    assert _modules(admin, s.cmp, ["rental", "nope"]).status_code == 400


def test_archive_of_the_last_tariff_needs_a_confirmation(owner: Client, s: Scenario) -> None:
    tariff = owner.get(f"/api/c/{s.cmp}/tariffs/{s.tariff}").json()["tariff"]
    refused = post(
        owner,
        f"/api/c/{s.cmp}/tariffs/{s.tariff}/archive",
        {"expected_version": tariff["version"]},
        key(),
    )
    # § 4.3.2: a warning — the location would be «not configured»; nothing is archived.
    assert refused.json()["error"]["code"] == "CONTEXT_WOULD_BE_UNCONFIGURED"
    assert refused.json()["error"]["params"]["locations"] == ["Phuket"]
    assert owner.get(f"/api/c/{s.cmp}/tariffs/{s.tariff}").json()["tariff"]["state"] == "active"
    done = post(
        owner,
        f"/api/c/{s.cmp}/tariffs/{s.tariff}/archive",
        {"expected_version": tariff["version"], "confirm_unconfigured": True},
        key(),
    )
    assert done.status_code == 200, done.content
    assert done.json()["unconfigured"] == ["Phuket"]
    assert AuditEvent.objects.filter(action="location.context_unconfigured").exists()


def test_another_tariff_keeps_the_location_ready(owner: Client, s: Scenario) -> None:
    second = post(
        owner,
        f"/api/c/{s.cmp}/tariffs",
        {"name": "Weekly", "ranges": [{"from": 1, "to": None, "rate": "900"}]},
        key(),
    )
    assert second.status_code == 201, second.content
    tariff = owner.get(f"/api/c/{s.cmp}/tariffs/{s.tariff}").json()["tariff"]
    done = post(
        owner,
        f"/api/c/{s.cmp}/tariffs/{s.tariff}/archive",
        {"expected_version": tariff["version"]},
        key(),
    )
    assert done.status_code == 200, done.content
    assert done.json()["unconfigured"] == []
    # A new location without a tariff of its own was never ready: nothing to warn about.
    add_location(owner, s.cmp, "Patong")


def test_staff_member_without_the_set_cannot_change_modules(s: Scenario) -> None:
    plain = member("plain@c4rsys.test", ["support"], unit="Support")
    assert _modules(plain, s.cmp, ["rental"]).status_code == 403
