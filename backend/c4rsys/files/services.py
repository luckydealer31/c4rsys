"""Storage of uploaded files (A10).

Content is kept on disk under `C4R_FILES_ROOT`, named by the file id; the database keeps details
and links. Disk or object storage is still OPEN in the architecture, so only this module touches
the disk. A file is closed by default: reading is allowed to its uploader and to whoever the
module owning the purpose admits (`register_reader`).
"""

import hashlib
import io
import zipfile
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

from django.conf import settings

from c4rsys.files.models import StoredFile
from c4rsys.platform import errors

# PROPOSAL (not confirmed by the customer): images only, up to 2 MB.
MAX_IMAGE_BYTES = 2 * 1024 * 1024
IMAGE_SIGNATURES: dict[str, Callable[[bytes], bool]] = {
    "image/png": lambda head: head.startswith(b"\x89PNG\r\n\x1a\n"),
    "image/jpeg": lambda head: head.startswith(b"\xff\xd8\xff"),
    "image/webp": lambda head: head[:4] == b"RIFF" and head[8:12] == b"WEBP",
}

# (user_id, file) -> may read; registered by the module that owns the purpose.
Reader = Callable[[UUID, StoredFile], bool]
_readers: dict[str, Reader] = {}


def register_reader(purpose: str, reader: Reader) -> None:
    _readers[purpose] = reader


def _root() -> Path:
    return Path(settings.C4R_FILES_ROOT)


def _path(file_id: UUID) -> Path:
    return _root() / str(file_id)


def image_type(content: bytes) -> str:
    """Content type by the file's own signature, not by the name or the browser's claim."""
    if not content:
        raise errors.validation_error("file", code="FIELD_REQUIRED")
    if len(content) > MAX_IMAGE_BYTES:
        raise errors.validation_error("file", code="FILE_TOO_LARGE", max_bytes=MAX_IMAGE_BYTES)
    for content_type, matches in IMAGE_SIGNATURES.items():
        if matches(content[:16]):
            return content_type
    raise errors.validation_error("file", code="FILE_TYPE_NOT_ALLOWED")


# Purposes that also take a PDF; others are images only.
DOCUMENT_PURPOSES = {"warehouse_document"}
# PROPOSAL: a scanned document may be larger than a photo.
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024


def document_type(content: bytes) -> str:
    """An image by `image_type`, or a PDF by its signature."""
    if not content:
        raise errors.validation_error("file", code="FIELD_REQUIRED")
    if len(content) > MAX_DOCUMENT_BYTES:
        raise errors.validation_error("file", code="FILE_TOO_LARGE", max_bytes=MAX_DOCUMENT_BYTES)
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    for content_type, matches in IMAGE_SIGNATURES.items():
        if matches(content[:16]):
            return content_type
    raise errors.validation_error("file", code="FILE_TYPE_NOT_ALLOWED")


# § 18.1.3: PDF, DOCX and XLSX up to 2 MB; photos by § 10.4.2 — JPEG, PNG, WebP up to 5 MB.
COMPANY_DOCUMENT = "company_document"
MAX_OFFICE_BYTES = 2 * 1024 * 1024
MAX_PHOTO_BYTES = 5 * 1024 * 1024
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _office_type(content: bytes) -> str | None:
    """DOCX and XLSX are zip packages told apart by their inner parts."""
    if not content.startswith(b"PK\x03\x04"):
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as package:
            names = package.namelist()
    except zipfile.BadZipFile:
        return None
    if any(n.startswith("word/") for n in names):
        return DOCX
    if any(n.startswith("xl/") for n in names):
        return XLSX
    return None


def company_document_type(content: bytes) -> str:
    """The type of a company document by its own content, with the limits of § 18.1.3."""
    if not content:
        raise errors.validation_error("file", code="FIELD_REQUIRED")
    for content_type, matches in IMAGE_SIGNATURES.items():
        if matches(content[:16]):
            if len(content) > MAX_PHOTO_BYTES:
                raise errors.validation_error(
                    "file", code="FILE_TOO_LARGE", max_bytes=MAX_PHOTO_BYTES
                )
            return content_type
    office = "application/pdf" if content.startswith(b"%PDF-") else _office_type(content)
    if office is None:
        raise errors.validation_error("file", code="FILE_TYPE_NOT_ALLOWED")
    if len(content) > MAX_OFFICE_BYTES:
        raise errors.validation_error("file", code="FILE_TOO_LARGE", max_bytes=MAX_OFFICE_BYTES)
    return office


def store(*, purpose: str, uploaded_by: UUID, content: bytes, content_type: str) -> StoredFile:
    """Writes the content, then the details. Call inside the command's transaction: if it is
    rolled back, an unreferenced file may remain on disk, but no record points to it."""
    item = StoredFile(
        purpose=purpose,
        uploaded_by=uploaded_by,
        content_type=content_type,
        size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )
    _root().mkdir(parents=True, exist_ok=True)
    _path(item.id).write_bytes(content)
    item.save(force_insert=True)
    return item


def usable(file_id: UUID, *, purpose: str, user_id: UUID, company_id: UUID | None = None) -> None:
    """A file may be linked only by its uploader, for its purpose, and not to another company."""
    item = StoredFile.objects.filter(pk=file_id, purpose=purpose, uploaded_by=user_id).first()
    if item is None or (item.company_id is not None and item.company_id != company_id):
        raise errors.validation_error("file", code="FILE_NOT_USABLE")


def attach_to_company(file_id: UUID, company_id: UUID) -> None:
    """Binds a logo to its company, so the company's readers may see it."""
    StoredFile.objects.filter(pk=file_id, company_id__isnull=True).update(company_id=company_id)


def readable(user_id: UUID, file_id: UUID) -> StoredFile | None:
    item = StoredFile.objects.filter(pk=file_id).first()
    if item is None:
        return None
    if item.uploaded_by == user_id:
        return item
    reader = _readers.get(item.purpose)
    return item if reader is not None and reader(user_id, item) else None


def content(item: StoredFile) -> bytes:
    return _path(item.id).read_bytes()
