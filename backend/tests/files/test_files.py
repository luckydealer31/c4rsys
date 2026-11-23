"""Uploaded images: closed by default, read by the uploader or the purpose's readers (A10)."""

from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
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
    # § 24.4.4: nothing usable is stored; the blocked attempt is kept without its content.
    assert not StoredFile.objects.exclude(state=StoredFile.State.BLOCKED).exists()


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


def _jpeg(width: int = 4, height: int = 3) -> bytes:
    """The header of a JPEG: enough for what takes the size of a photo from it."""
    frame = b"\x08" + height.to_bytes(2, "big") + width.to_bytes(2, "big") + b"\x03" + b"\x00" * 9
    return (
        b"\xff\xd8\xff\xe0\x00\x04JF"
        + b"\xff\xc0"
        + (len(frame) + 2).to_bytes(2, "big")
        + frame
        + b"\xff\xd9"
    )


def _photos(client: Client, purpose: str, photos: list[bytes], key: str = "p-1") -> Any:
    return client.post(
        f"/api/files?purpose={purpose}",
        data={"file": [SimpleUploadedFile(f"{n}.jpg", photo) for n, photo in enumerate(photos)]},
        headers={"Idempotency-Key": key},
    )


def test_several_photos_become_the_pages_of_one_document(client: Client) -> None:
    """Customer's decision of 23.11.2026: a paper of a vehicle — up to 5 photos, a document of a
    supplier — up to 10, a receipt of an expense — up to 3."""
    register(client)

    response = _photos(client, "vehicle_paper", [_jpeg(4, 3), _jpeg(6, 8)])

    assert response.status_code == 201, response.content
    assert response.json()["file"]["content_type"] == "application/pdf"
    kept = client.get(f"/api/files/{response.json()['file']['id']}").content
    assert kept.startswith(b"%PDF-") and b"/Count 2" in kept and kept.count(_jpeg(6, 8)) == 1

    for purpose, most in (("vehicle_paper", 5), ("warehouse_document", 10), ("expense_receipt", 3)):
        fits = _photos(client, purpose, [_jpeg()] * most, key=f"fits-{purpose}")
        assert fits.status_code == 201, fits.content
        over = _photos(client, purpose, [_jpeg()] * (most + 1), key=f"over-{purpose}")
        assert over.json()["error"]["code"] == "TOO_MANY_PAGES"
        assert over.json()["error"]["params"]["max"] == most
    # Where one photo is meant, several are not taken; a page is a JPEG only.
    assert _photos(client, "avatar", [_jpeg(), _jpeg()]).json()["error"]["code"] == "TOO_MANY_PAGES"
    mixed = _photos(client, "vehicle_paper", [_jpeg(), PNG], key="mixed")
    assert mixed.json()["error"]["code"] == "PAGES_NOT_PHOTOS"
