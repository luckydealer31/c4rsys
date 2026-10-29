"""Documents, part Р1: the registry, upload, versions, links, scope, term and archive (ТЗ § 18.1,
§ 18.5, § 23.8.1; customer's decisions Р-5…Р-11 of 29.10.2026)."""

import io
import zipfile
from datetime import timedelta
from typing import Any

import pytest
from django.test import Client
from django.utils import timezone

from tests.accounts.helpers import post
from tests.companies.helpers import hire, key
from tests.files.helpers import upload
from tests.rentals.scenario import Scenario, add_location, build

pytestmark = pytest.mark.django_db

PDF = b"%PDF-1.4\n" + b"0" * 100


def _docx() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        package.writestr("word/document.xml", "<w:document/>")
    return buffer.getvalue()


@pytest.fixture
def s(owner: Client) -> Scenario:
    return build(owner)


def _file(client: Client, content: bytes = PDF) -> str:
    response = upload(client, "company_document", content, key=key()["Idempotency-Key"])
    assert response.status_code == 201, response.content
    file_id: str = response.json()["file"]["id"]
    return file_id


def _kind(client: Client, s: Scenario, code: str = "contract") -> str:
    kinds = client.get(f"/api/c/{s.cmp}/document-kinds").json()["kinds"]
    kind_id: str = next(k["id"] for k in kinds if k["code"] == code)
    return kind_id


def _create(client: Client, s: Scenario, **body: Any) -> Any:
    data = {
        "title": "Insurance",
        "kind_id": _kind(client, s),
        "file_id": _file(client),
        "file_name": "insurance.pdf",
        **body,
    }
    return post(client, f"/api/c/{s.cmp}/documents", data, key())


def _ok(response: Any, status: int = 200) -> dict[str, Any]:
    assert response.status_code == status, response.content
    d: dict[str, Any] = response.json()["document"]
    return d


def _do(client: Client, s: Scenario, d: dict[str, Any], action: str, **body: Any) -> Any:
    data = {"expected_version": d["version"], **body}
    return post(client, f"/api/c/{s.cmp}/documents/{d['id']}/{action}", data, key())


def test_file_types_and_limits(owner: Client) -> None:
    # § 18.1.3: PDF, DOCX, XLSX up to 2 MB; photos up to 5 MB.
    assert (
        upload(owner, "company_document", _docx(), key="a")
        .json()["file"]["content_type"]
        .endswith("wordprocessingml.document")
    )
    big = PDF + b"0" * (2 * 1024 * 1024)
    assert (
        upload(owner, "company_document", big, key="b").json()["error"]["code"] == "FILE_TOO_LARGE"
    )
    text = upload(owner, "company_document", b"hello world", key="c")
    assert text.json()["error"]["code"] == "FILE_TYPE_NOT_ALLOWED"


def test_registry_numbers_links_and_default_scope(owner: Client, s: Scenario) -> None:
    free = _ok(_create(owner, s, title="Licence", kind_id=_kind(owner, s, "internal")), 201)
    # § 18.1.10: without links — in the registry, for the whole company.
    assert free["number"] == 1 and free["all_locations"] and free["links"] == []
    linked = _ok(_create(owner, s, links=[{"object_type": "vehicle", "object_id": s.vehicle}]), 201)
    # Decision Р-11: the scope of a vehicle document is its location.
    assert linked["number"] == 2 and linked["locations"] == [s.location]
    seen = owner.get(f"/api/c/{s.cmp}/documents?object_type=vehicle&object_id={s.vehicle}")
    assert [d["id"] for d in seen.json()["documents"]] == [linked["id"]]
    # § 18.1.9: the same document from another card, without a copy.
    d = _ok(_do(owner, s, linked, "links", object_type="client", object_id=s.client))
    assert {x["object_type"] for x in d["links"]} == {"vehicle", "client"}
    assert linked["locations"] == d["locations"]


def test_scope_limits_viewing_and_the_file(owner: Client, s: Scenario) -> None:
    other = add_location(owner, s.cmp, "Krabi")
    clerk, _ = hire(
        owner,
        s.cmp,
        "clerk@example.com",
        [{"type": "grant", "permission": "documents.view", "locations": [other]}],
    )
    d = _ok(_create(owner, s, links=[{"object_type": "vehicle", "object_id": s.vehicle}]), 201)
    # § 18.5.3–18.5.4: a link does not open the document outside its scope.
    assert clerk.get(f"/api/c/{s.cmp}/documents/{d['id']}").status_code == 404
    file_id = d["current_version"]["file_id"]
    assert clerk.get(f"/api/files/{file_id}").status_code == 404
    assert owner.get(f"/api/files/{file_id}").status_code == 200


def test_term_warning_version_and_archive(owner: Client, s: Scenario) -> None:
    today = timezone.localdate()
    d = _ok(
        _create(owner, s, valid_until=(today + timedelta(days=5)).isoformat(), warn_days=10),
        201,
    )
    assert d["warning"] == "expiring"
    warned = owner.get(f"/api/c/{s.cmp}/documents?warnings=1").json()["documents"]
    assert [x["id"] for x in warned] == [d["id"]]
    # § 18.1.14: an extension is a new version; the former one stays.
    d = _ok(
        _do(
            owner,
            s,
            d,
            "versions",
            file_id=_file(owner),
            valid_until=(today + timedelta(days=365)).isoformat(),
            note="Renewed",
        )
    )
    assert d["warning"] is None and [v["number"] for v in d["versions"]] == [2, 1]
    assert _do(owner, s, d, "archive").json()["error"]["code"] == "FIELD_REQUIRED"
    d = _ok(_do(owner, s, d, "archive", reason="Vehicle sold"))
    assert d["state"] == "archived"
    assert owner.get(f"/api/c/{s.cmp}/documents").json()["documents"] == []


def test_rights(owner: Client, s: Scenario) -> None:
    viewer, _ = hire(
        owner, s.cmp, "viewer@example.com", [{"type": "grant", "permission": "documents.view"}]
    )
    d = _ok(_create(owner, s), 201)
    assert viewer.get(f"/api/c/{s.cmp}/documents/{d['id']}").status_code == 200
    assert _create(viewer, s).status_code == 403
    assert _do(viewer, s, d, "archive", reason="x").status_code == 403
