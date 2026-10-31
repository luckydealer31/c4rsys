"""The check of uploaded files before access and their safe serving (ТЗ § 24.4.4)."""

import io
import zipfile
from typing import Any

import pytest
from django.test import Client

from c4rsys.files import services
from c4rsys.files.models import StoredFile
from c4rsys.platform.models import AuditEvent
from tests.accounts.helpers import register
from tests.companies.helpers import create_company, hire, named_user
from tests.files.helpers import PNG, upload

pytestmark = pytest.mark.django_db

PDF = b"%PDF-1.4\n" + b"0" * 100


def _package(*names: str, types: str = "") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        package.writestr("[Content_Types].xml", types or "<Types/>")
        package.writestr("word/document.xml", "<w:document/>")
        for name in names:
            package.writestr(name, "x")
    return buffer.getvalue()


def _code(response: Any) -> str:
    code: str = response.json()["error"]["code"]
    return code


def test_an_unsupported_file_is_blocked_and_journaled(client: Client) -> None:
    register(client)
    response = upload(client, "company_document", b"<html><script>alert(1)</script></html>")
    assert response.status_code == 400
    assert _code(response) == "FILE_TYPE_NOT_ALLOWED"
    blocked = StoredFile.objects.get()
    assert blocked.state == StoredFile.State.BLOCKED
    assert blocked.blocked_reason == "type_not_allowed"
    # Its content is not kept and nobody reads it, the uploader neither.
    assert not (services._root() / str(blocked.id)).exists()
    assert client.get(f"/api/files/{blocked.id}").status_code == 404
    event = AuditEvent.objects.get(action="file.blocked")
    assert event.result == "blocked" and (event.after or {}).get("reason") == "type_not_allowed"


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (_package("word/vbaProject.bin"), "active_content"),
        (
            _package(
                types='<Override ContentType="application/vnd.ms-word.document.macroEnabled"/>'
            ),
            "active_content",
        ),
        (_package("word/embeddings/payload.exe"), "active_content"),
        (
            b"%PDF-1.4\n1 0 obj << /OpenAction << /S /JavaScript /JS (app.alert(1)) >> >>",
            "active_content",
        ),
        (b"%PDF-1.4\n1 0 obj << /Type /Action /S /Launch /F (cmd.exe) >>", "active_content"),
    ],
)
def test_dangerous_content_of_an_allowed_format_is_blocked(
    client: Client, content: bytes, reason: str
) -> None:
    register(client)
    response = upload(client, "company_document", content)
    assert response.status_code == 400, response.content
    assert _code(response) == "FILE_BLOCKED"
    assert response.json()["error"]["params"]["reason"] == reason
    assert StoredFile.objects.get().state == StoredFile.State.BLOCKED
    assert AuditEvent.objects.filter(action="file.blocked").count() == 1


def test_a_json_key_is_not_a_script(client: Client) -> None:
    register(client)
    content = PDF + b"<< /JSON (data) >>"
    assert upload(client, "company_document", content).status_code == 201


def test_a_file_in_checking_is_seen_by_its_uploader_only(client: Client) -> None:
    named_user(client)
    logo = upload(client, "company_logo").json()["file"]["id"]
    company = create_company(client, logo=logo).json()["company"]
    employee, _ = hire(client, company["public_id"], "staff@example.com", [])
    StoredFile.objects.filter(pk=logo).update(state=StoredFile.State.CHECKING)
    assert client.get(f"/api/files/{logo}").status_code == 200
    assert employee.get(f"/api/files/{logo}").status_code == 404
    StoredFile.objects.filter(pk=logo).update(state=StoredFile.State.READY)
    assert employee.get(f"/api/files/{logo}").status_code == 200


def test_a_checked_file_is_ready_and_a_reupload_is_a_new_file(client: Client) -> None:
    register(client)
    first = upload(client, "avatar").json()["file"]
    second = upload(client, "avatar", key="f-2").json()["file"]
    assert first["state"] == second["state"] == "ready"
    assert first["id"] != second["id"]
    assert StoredFile.objects.get(pk=first["id"]).checked_at is not None


def test_a_file_in_checking_is_not_linked(client: Client) -> None:
    register(client)
    file_id = upload(client, "avatar").json()["file"]["id"]
    StoredFile.objects.filter(pk=file_id).update(state=StoredFile.State.CHECKING)
    response = client.post(
        "/api/me/personal-data",
        data={"first_name": "Anna", "last_name": "Petrova", "avatar": file_id},
        content_type="application/json",
        headers={"Idempotency-Key": "a-1"},
    )
    assert response.status_code == 400
    assert _code(response) == "FILE_NOT_USABLE"


def test_files_are_served_with_safe_headers(client: Client) -> None:
    register(client)
    image = upload(client, "avatar").json()["file"]["id"]
    response = client.get(f"/api/files/{image}")
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Content-Disposition"] == "inline"
    assert "sandbox" in response["Content-Security-Policy"]
    assert response.content == PNG
    docx = upload(client, "company_document", _package(), key="d-1").json()["file"]["id"]
    saved = client.get(f"/api/files/{docx}")
    # Not an image or a PDF: only saved, never shown in place.
    assert saved["Content-Disposition"] == "attachment"
    assert saved["X-Content-Type-Options"] == "nosniff"
