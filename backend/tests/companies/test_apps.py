"""The company connects its applications itself (ТЗ § 4.1.9, § 22.1.3; customer's decisions of
16.11.2026): a new company has none, a connection names the locations where the application
works, and whoever may change the settings of the company connects and disconnects."""

from typing import Any

import pytest
from django.test import Client

from c4rsys.companies.models import ActivityContext
from c4rsys.platform.models import AuditEvent
from tests.accounts.helpers import post
from tests.companies.helpers import create_company, hire, key

pytestmark = pytest.mark.django_db


@pytest.fixture
def cmp(owner: Client) -> str:
    """A company as the product creates it: without applications."""
    response = create_company(owner, timezone="Asia/Bangkok", apps=())
    public_id: str = response.json()["company"]["public_id"]
    return public_id


def _location(client: Client, cmp: str, name: str = "Phuket", **data: Any) -> Any:
    return post(client, f"/api/c/{cmp}/locations", {"name": name, **data}, headers=key())


def _connect(client: Client, cmp: str, module: str, locations: list[str] | None = None) -> Any:
    body = {} if locations is None else {"locations": locations}
    return post(client, f"/api/c/{cmp}/modules/{module}/connect", body, headers=key())


def _disconnect(client: Client, cmp: str, module: str) -> Any:
    return post(client, f"/api/c/{cmp}/modules/{module}/disconnect", {}, headers=key())


def _company(client: Client, cmp: str) -> dict[str, Any]:
    company: dict[str, Any] = client.get(f"/api/c/{cmp}").json()["company"]
    return company


def _code(response: Any) -> str:
    code: str = response.json()["error"]["code"]
    return code


def test_new_company_has_no_application(owner: Client, cmp: str) -> None:
    assert _company(owner, cmp)["modules"] == []
    # A context of an application that is not connected cannot be added (§ 4.1.9) — neither with
    # a new location nor to an existing one.
    assert _code(_location(owner, cmp, contexts=["rental"])) == "MODULE_NOT_CONNECTED"
    location_id = _location(owner, cmp).json()["location"]["id"]
    refused = post(
        owner, f"/api/c/{cmp}/locations/{location_id}/contexts", {"type": "rental"}, headers=key()
    )
    assert _code(refused) == "MODULE_NOT_CONNECTED"


def test_connection_without_locations_only_connects(owner: Client, cmp: str) -> None:
    response = _connect(owner, cmp, "rental")
    assert response.status_code == 200, response.content
    assert response.json()["company"]["modules"] == ["rental"]
    assert _company(owner, cmp)["context_types"] == []
    entry = AuditEvent.objects.get(action="company.modules_changed")
    assert entry.before == {"modules": []} and entry.after == {"modules": ["rental"]}
    # From now the context is added with a location.
    created = _location(owner, cmp, contexts=["rental"])
    assert created.status_code == 201, created.content
    # The applications keep the order of the catalog, not the order of connection.
    assert _connect(owner, cmp, "warehouse").json()["company"]["modules"] == [
        "rental",
        "warehouse",
    ]
    assert _connect(owner, cmp, "service").json()["company"]["modules"] == [
        "rental",
        "service",
        "warehouse",
    ]


def test_connection_adds_the_context_to_the_chosen_locations(owner: Client, cmp: str) -> None:
    first = _location(owner, cmp, "Phuket").json()["location"]["id"]
    second = _location(owner, cmp, "Samui").json()["location"]["id"]
    third = _location(owner, cmp, "Krabi").json()["location"]["id"]

    response = _connect(owner, cmp, "service", [first, third])

    assert response.status_code == 200, response.content
    assert response.json()["company"]["context_types"] == ["service"]
    contexts = {str(c.location_id): c.state for c in ActivityContext.objects.filter(type="service")}
    assert contexts == {first: "enabled", third: "enabled"}
    assert second not in contexts
    assert AuditEvent.objects.filter(action="location.context_added").count() == 2


def test_connection_is_refused_as_a_whole(owner: Client, cmp: str) -> None:
    location_id = _location(owner, cmp).json()["location"]["id"]
    assert _connect(owner, cmp, "taxi").status_code == 400
    assert _connect(owner, cmp, "rental", ["not-an-id"]).status_code == 400
    assert _connect(owner, cmp, "rental", [location_id, location_id]).status_code == 400
    # A location of nobody: nothing of the command stays, the application included.
    unknown = "018f0000-0000-7000-8000-000000000000"
    assert _connect(owner, cmp, "rental", [location_id, unknown]).status_code == 404
    assert _company(owner, cmp)["modules"] == []
    assert not ActivityContext.objects.exists()

    assert _connect(owner, cmp, "rental", [location_id]).status_code == 200
    assert _code(_connect(owner, cmp, "rental", [location_id])) == "MODULE_ALREADY_CONNECTED"


def test_disconnection_waits_for_the_contexts_and_reconnection_restores_them(
    owner: Client, cmp: str
) -> None:
    location_id = _location(owner, cmp).json()["location"]["id"]
    assert _connect(owner, cmp, "warehouse", [location_id]).status_code == 200
    context = ActivityContext.objects.get(type="warehouse")

    # Not while the application works in a location: its context is switched off first (§ 4.4.2).
    assert _code(_disconnect(owner, cmp, "warehouse")) == "MODULE_HAS_CONTEXTS"
    path = f"/api/c/{cmp}/locations/{location_id}/contexts/{context.id}"
    assert post(owner, f"{path}/disable", {}, headers=key()).status_code == 200
    off = _disconnect(owner, cmp, "warehouse")
    assert off.status_code == 200, off.content
    assert off.json()["company"]["modules"] == []
    assert _code(_disconnect(owner, cmp, "warehouse")) == "MODULE_NOT_CONNECTED"
    # The switched-off context cannot be switched on while the application is not connected…
    # (it keeps its history and its ID, § 4.1.7–4.1.8)
    assert ActivityContext.objects.get(pk=context.id).state == "disabled"

    # …and the same context comes back with the connection.
    assert _connect(owner, cmp, "warehouse", [location_id]).status_code == 200
    restored = ActivityContext.objects.get(type="warehouse")
    assert restored.id == context.id and restored.state == "enabled"


def test_only_who_changes_the_company_settings_connects(owner: Client, cmp: str) -> None:
    plain, _ = hire(owner, cmp, "plain@example.com", [])
    setter, _ = hire(
        owner, cmp, "setter@example.com", [{"type": "grant", "permission": "company.settings"}]
    )

    assert _connect(plain, cmp, "rental").status_code == 403
    assert _company(owner, cmp)["modules"] == []
    assert _connect(setter, cmp, "rental").status_code == 200
    assert _disconnect(plain, cmp, "rental").status_code == 403
    assert _disconnect(setter, cmp, "rental").status_code == 200


def test_wizard_section_of_applications(owner: Client, cmp: str) -> None:
    def section() -> bool:
        done: bool = owner.get(f"/api/c/{cmp}/readiness").json()["sections"]["apps"]
        return done

    assert section() is False
    assert _connect(owner, cmp, "rental").status_code == 200
    assert section() is True
