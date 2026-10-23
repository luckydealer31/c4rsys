"""Uploaded images: closed by default, read by the uploader or the purpose's readers (A10)."""

import pytest
from django.test import Client

from c4rsys.files.models import StoredFile
from c4rsys.platform.models import AuditEvent
from tests.accounts.helpers import register
from tests.companies.helpers import create_company, hire, named_user
from tests.files.helpers import PNG, upload

pytestmark = pytest.mark.django_db


def test_uploaded_image_is_read_by_its_uploader_only(client: Client) -> None:
    register(client)
    response = upload(client, "avatar")
    assert response.status_code == 201, response.content
    file_id = response.json()["file"]["id"]

    own = client.get(f"/api/files/{file_id}")
    assert own.status_code == 200 and own["Content-Type"] == "image/png"
    assert own.content == PNG
    other = Client()
    register(other, "other@example.com")
    assert other.get(f"/api/files/{file_id}").status_code == 404
    assert AuditEvent.objects.filter(action="file.uploaded").count() == 1


def test_only_png_jpeg_webp_are_accepted(client: Client) -> None:
    register(client)

    response = upload(client, "avatar", b"%PDF-1.7 not an image")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "FILE_TYPE_NOT_ALLOWED"
    assert not StoredFile.objects.exists()


def test_image_over_two_megabytes_is_refused(client: Client) -> None:
    register(client)

    response = upload(client, "avatar", PNG + b"\x00" * (2 * 1024 * 1024))

    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"


def test_company_logo_at_creation_is_seen_by_employees(client: Client) -> None:
    named_user(client)
    logo = upload(client, "company_logo").json()["file"]["id"]

    company = create_company(client, logo=logo).json()["company"]
    assert company["logo"] == logo

    employee, _ = hire(client, company["public_id"], "staff@example.com", [])
    assert employee.get(f"/api/files/{logo}").status_code == 200
    stranger = Client()
    register(stranger, "stranger@example.com")
    assert stranger.get(f"/api/files/{logo}").status_code == 404


def test_logo_of_another_user_cannot_be_used(client: Client) -> None:
    named_user(client)
    other = Client()
    named_user(other, "other@example.com")
    logo = upload(other, "company_logo").json()["file"]["id"]

    response = create_company(client, logo=logo)

    assert response.json()["error"]["code"] == "FILE_NOT_USABLE"
