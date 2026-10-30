"""The full export of the company's data (ТЗ § 22.6.1, § 2.8.1; decision Б-6; part К7)."""

import csv
import io
import zipfile
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from django.test import Client
from django.utils import timezone

from c4rsys.documents import services as documents
from c4rsys.exports import services
from c4rsys.exports.models import DataExport
from c4rsys.notifications import services as notifications
from c4rsys.platform.models import AuditEvent
from tests.accounts.helpers import post
from tests.companies.helpers import create_company, hire, key
from tests.documents.test_registry import _create, _ok
from tests.rentals.scenario import Scenario

pytestmark = pytest.mark.django_db


def export(client: Client, s: Scenario, capture: Any) -> dict[str, object]:
    # The archive takes its name when the transaction commits.
    with capture(execute=True):
        created = post(client, f"/api/c/{s.cmp}/exports", {}, key())
    assert created.status_code == 201, created.content
    result: dict[str, object] = created.json()["export"]
    return result


def download(client: Client, s: Scenario, export_id: object) -> zipfile.ZipFile:
    response = client.get(f"/api/c/{s.cmp}/exports/{export_id}/download")
    assert response.status_code == 200, response.content
    return zipfile.ZipFile(io.BytesIO(b"".join(response.streaming_content)))  # type: ignore[attr-defined]


def rows(package: zipfile.ZipFile, table: str) -> list[dict[str, str]]:
    text = package.read(f"tables/{table}.csv").decode("utf-8")
    return list(csv.DictReader(io.StringIO(text)))


def test_tables_with_ids_and_links_and_document_files(
    owner: Client, s: Scenario, django_capture_on_commit_callbacks: Any
) -> None:
    doc = _ok(_create(owner, s, links=[{"object_type": "client", "object_id": s.client}]), 201)
    # Another company's data never get into the export.
    create_company(owner, key="c-2", display_name="Other")

    made = export(owner, s, django_capture_on_commit_callbacks)
    assert made["available"] and made["documents"] == 1
    package = download(owner, s, made["id"])

    clients = rows(package, "client_card")
    assert [c["id"] for c in clients] == [s.client]
    links = rows(package, "document_link")
    assert links[0]["document_id"] == doc["id"] and links[0]["object_id"] == s.client
    assert [c["display_name"] for c in rows(package, "company")] == ["Test Rent"]
    cards = rows(package, "vehicle_card")
    assert [v["id"] for v in rows(package, "vehicle")] == [cards[0]["vehicle_id"]]
    assert rows(package, "employee_names")[0]["last_name"] == "Petrova"
    assert any(name.startswith("documents/1/v1_") for name in package.namelist())
    # Secrets are not written.
    signing = package.read("tables/document_signer.csv").decode("utf-8")
    assert "code_hash" not in signing
    assert AuditEvent.objects.filter(action="data.export_downloaded").count() == 1


def test_right_scope_and_the_24_hours(
    owner: Client, s: Scenario, django_capture_on_commit_callbacks: Any
) -> None:
    viewer, _ = hire(
        owner,
        s.cmp,
        "viewer@example.com",
        [{"type": "grant", "permission": "documents.view"}],
    )
    assert post(viewer, f"/api/c/{s.cmp}/exports", {}, key()).status_code == 403
    made = export(owner, s, django_capture_on_commit_callbacks)
    # § 2.8.1: the link needs the current right, not only the address.
    url = f"/api/c/{s.cmp}/exports/{made['id']}/download"
    assert viewer.get(url).status_code == 403
    assert Client().get(url).status_code == 401

    DataExport.objects.filter(pk=str(made["id"])).update(expires_at=timezone.now() - timedelta(1))
    late = owner.get(url)
    assert late.json()["error"]["code"] == "EXPORT_EXPIRED"
    listed = owner.get(f"/api/c/{s.cmp}/exports").json()["exports"]
    assert listed[0]["available"] is False

    # The file is removed by the periodic check, with a journal record.
    stored = services.path_of(DataExport.objects.get(pk=str(made["id"])))
    assert stored.exists()
    with django_capture_on_commit_callbacks(execute=True):
        notifications.run_checks()
    assert DataExport.objects.get(pk=str(made["id"])).removed_at is not None
    assert not stored.exists()
    removed = AuditEvent.objects.get(action="data.export_removed")
    assert removed.system_process == "data_export_retention"
    assert removed.object_id == str(made["id"])


def test_entry_names_are_plain_and_unique(
    owner: Client, s: Scenario, django_capture_on_commit_callbacks: Any
) -> None:
    # Names given by users may hold a path: they never leave the folder of the document (versions
    # are append-only, so the name is checked on the function that builds the entry).
    assert documents.safe_file_name("../../etc/x.pdf", "f") == "x.pdf"
    assert documents.safe_file_name("..", "f") == "f"

    used: set[str] = set()
    assert services._entry("1/v1_a.pdf", used) == "documents/1/v1_a.pdf"
    assert services._entry("1/v1_a.pdf", used) == "documents/1/v1_a_2.pdf"
    assert services._entry("1/v1_a.pdf", used) == "documents/1/v1_a_3.pdf"
    assert services._entry("/../1/./b", used) == "documents/1/b"


def test_a_rolled_back_export_leaves_no_archive(
    owner: Client, s: Scenario, files_root: Path
) -> None:
    with mock.patch.object(DataExport, "save", side_effect=RuntimeError("boom")):
        failed = post(owner, f"/api/c/{s.cmp}/exports", {}, key())
    assert failed.status_code == 500
    assert not DataExport.objects.exists()
    assert list((files_root / "exports").glob("*")) == []

    # An archive of an export that never committed is removed after the life of a link.
    left = files_root / "exports" / "abandoned.zip.part"
    left.write_bytes(b"x")
    services.purge_expired(timezone.now() + services.TTL + timedelta(minutes=1))
    assert not left.exists()
