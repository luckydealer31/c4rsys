"""Documents, part Р2: templates, fields and documents made by them (ТЗ § 18.1.4–18.1.6, § 18.2,
§ 18.5.2; customer's decisions Р-1…Р-3 of 29.10.2026)."""

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from django.test import Client

from c4rsys.documents.models import DocumentTemplate
from c4rsys.rentals import issue as issue_module
from tests.accounts.helpers import post
from tests.companies.helpers import key
from tests.rentals.scenario import NOW, Scenario, issue

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def frozen() -> Iterator[None]:
    with mock.patch.object(issue_module, "timezone", SimpleNamespace(now=lambda: NOW)):
        yield


def _template(client: Client, s: Scenario, code: str, language: str = "ru") -> dict[str, Any]:
    items = client.get(f"/api/c/{s.cmp}/document-templates").json()["templates"]
    found: dict[str, Any] = next(
        t for t in items if t["code"] == code and t["language"] == language
    )
    return found


def _make(client: Client, s: Scenario, template_id: str, source_id: str | None, **body: Any) -> Any:
    data = {"template_id": template_id, "source_id": source_id, **body}
    return post(client, f"/api/c/{s.cmp}/documents/from-template", data, key())


def _content(d: dict[str, Any]) -> str:
    content: str = d["versions"][0]["content"]
    return content


def test_starting_templates_in_three_languages(owner: Client, s: Scenario) -> None:
    # Seven starting templates, the act of inspection among them (decision В-8.3).
    assert DocumentTemplate.objects.filter(company__isnull=True).count() == 24
    contract = _template(owner, s, "rental_contract", "th")
    assert contract["system"] and contract["source"] == "rental"


def test_contract_by_template_fixes_values_and_links(owner: Client, s: Scenario) -> None:
    rental = issue(owner, s).json()["rental"]
    contract = _template(owner, s, "rental_contract")
    preview = post(
        owner,
        f"/api/c/{s.cmp}/documents/preview",
        {"template_id": contract["id"], "source_id": rental["id"]},
        key(),
    ).json()
    assert preview["missing_required"] == []
    assert "Petrov Ivan" in preview["html"] and "Toyota Yaris" in preview["html"]
    made = _make(owner, s, contract["id"], rental["id"])
    assert made.status_code == 201, made.content
    d = made.json()["document"]
    assert f"№ {d['number']}" in _content(d)
    # Empty optional fields stay empty in the made document (§ 18.2.1).
    assert "{{" not in _content(d) and "missing" not in _content(d)
    assert {x["object_type"] for x in d["links"]} == {"rental", "client", "vehicle", "order"}
    assert d["locations"] == [s.location] and d["template_version"] == 1
    # § 18.2.4: a later change of the client card does not rewrite the document.
    owner.patch(
        f"/api/c/{s.cmp}/clients/{s.client}",
        data={"first_name": "Pyotr", "expected_version": 1},
        content_type="application/json",
        headers=key(),
    )
    again = owner.get(f"/api/c/{s.cmp}/documents/{d['id']}").json()["document"]
    assert "Petrov Ivan" in _content(again)


def test_required_field_without_data_stops_making(owner: Client, s: Scenario) -> None:
    rental = issue(owner, s).json()["rental"]
    # § 18.2.1: the return act needs the end of the rental, not known yet.
    refused = _make(owner, s, _template(owner, s, "return_act")["id"], rental["id"])
    assert refused.json()["error"]["code"] == "TEMPLATE_REQUIRED_FIELDS_MISSING"
    assert refused.json()["error"]["params"]["fields"] == ["rental.end"]


def test_company_template_versions_and_remake(owner: Client, s: Scenario) -> None:
    rental = issue(owner, s).json()["rental"]
    starter = _template(owner, s, "issue_act")
    copied = post(owner, f"/api/c/{s.cmp}/document-templates", {"copy_from": starter["id"]}, key())
    assert copied.status_code == 201, copied.content
    own = copied.json()["template"]
    d = _make(owner, s, own["id"], rental["id"]).json()["document"]
    changed = owner.patch(
        f"/api/c/{s.cmp}/document-templates/{own['id']}",
        data={
            **{
                k: own[k]
                for k in ("title", "kind_id", "source", "required_fields", "signers", "language")
            },
            "content": "# Act\n{{vehicle.name}} {{vehicle.plate}}\n{{rental.start}}",
            "expected_version": own["version"],
        },
        content_type="application/json",
        headers=key(),
    )
    assert changed.status_code == 200, changed.content
    assert changed.json()["template"]["template_version"] == 2
    # § 18.5.2: the made document keeps its version until it is made again explicitly.
    kept = owner.get(f"/api/c/{s.cmp}/documents/{d['id']}").json()["document"]
    assert kept["template_version"] == 1
    remade = post(
        owner,
        f"/api/c/{s.cmp}/documents/{d['id']}/remake",
        {"expected_version": kept["version"]},
        key(),
    )
    assert remade.status_code == 200, remade.content
    d = remade.json()["document"]
    assert d["template_version"] == 2 and [v["number"] for v in d["versions"]] == [2, 1]


def test_unknown_or_unavailable_fields_are_refused(owner: Client, s: Scenario) -> None:
    kind = owner.get(f"/api/c/{s.cmp}/document-kinds").json()["kinds"][0]["id"]
    body = {"title": "X", "kind_id": kind, "source": "client", "content": "{{client.shoe_size}}"}
    unknown = post(owner, f"/api/c/{s.cmp}/document-templates", body, key())
    assert unknown.json()["error"]["code"] == "TEMPLATE_FIELD_UNKNOWN"
    body["content"] = "{{rental.start}}"
    wrong = post(owner, f"/api/c/{s.cmp}/document-templates", body, key())
    assert wrong.json()["error"]["code"] == "TEMPLATE_FIELD_NOT_AVAILABLE"


def test_values_are_escaped(owner: Client, s: Scenario) -> None:
    kind = owner.get(f"/api/c/{s.cmp}/document-kinds").json()["kinds"][0]["id"]
    own = post(
        owner,
        f"/api/c/{s.cmp}/document-templates",
        {
            "title": "Card",
            "kind_id": kind,
            "source": "client",
            "content": "**Name:** {{client.full_name}}",
        },
        key(),
    ).json()["template"]
    owner.patch(
        f"/api/c/{s.cmp}/clients/{s.client}",
        data={"first_name": "<script>x</script>", "expected_version": 1},
        content_type="application/json",
        headers=key(),
    )
    d = _make(owner, s, own["id"], s.client).json()["document"]
    content = _content(d)
    assert "<script>" not in content and "&lt;script&gt;" in content
    assert "<strong>Name:</strong>" in content
