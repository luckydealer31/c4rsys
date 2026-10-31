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
from django.db import transaction
from django.http import HttpResponse
from django.http.response import HttpResponseBase
from django.utils import timezone

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


# (user_id, file, kind) -> None: the module owning the purpose journals a read (§ 24.4.1).
ReadHook = Callable[[UUID, StoredFile, str], None]
_read_hooks: dict[str, ReadHook] = {}


def register_read_hook(purpose: str, hook: ReadHook) -> None:
    _read_hooks[purpose] = hook


def read_hooks(user_id: UUID, item: StoredFile, kind: str) -> None:
    hook = _read_hooks.get(item.purpose)
    if hook is not None:
        hook(user_id, item, kind)


def _root() -> Path:
    return Path(settings.C4R_FILES_ROOT)


def _path(file_id: UUID) -> Path:
    return _root() / str(file_id)


def too_large(limit: int) -> errors.ApiError:
    """The limit in bytes and in whole megabytes, for the message."""
    return errors.validation_error(
        "file", code="FILE_TOO_LARGE", max_bytes=limit, max_mb=limit // (1024 * 1024)
    )


def image_type(content: bytes) -> str:
    """Content type by the file's own signature, not by the name or the browser's claim."""
    if not content:
        raise errors.validation_error("file", code="FIELD_REQUIRED")
    if len(content) > MAX_IMAGE_BYTES:
        raise too_large(MAX_IMAGE_BYTES)
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
        raise too_large(MAX_DOCUMENT_BYTES)
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    for content_type, matches in IMAGE_SIGNATURES.items():
        if matches(content[:16]):
            return content_type
    raise errors.validation_error("file", code="FILE_TYPE_NOT_ALLOWED")


# § 18.1.3: PDF, DOCX and XLSX up to 2 MB; photos by § 10.4.2 — JPEG, PNG, WebP up to 5 MB.
COMPANY_DOCUMENT = "company_document"
TASK_ATTACHMENT = "task_attachment"
# A file of a global document of a vehicle (§ 8.2.3); PROPOSAL: the types and sizes of a company
# document.
VEHICLE_DOCUMENT = "vehicle_document"
# Decision З-10: an attachment of a task takes the types and sizes of a company document.
OFFICE_PURPOSES = {COMPANY_DOCUMENT, TASK_ATTACHMENT, VEHICLE_DOCUMENT}
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
                raise too_large(MAX_PHOTO_BYTES)
            return content_type
    office = "application/pdf" if content.startswith(b"%PDF-") else _office_type(content)
    if office is None:
        raise errors.validation_error("file", code="FILE_TYPE_NOT_ALLOWED")
    if len(content) > MAX_OFFICE_BYTES:
        raise too_large(MAX_OFFICE_BYTES)
    return office


INSPECTION_PHOTO = "inspection_photo"
INSPECTION_VIDEO = "inspection_video"
# § 10.4.2 limits a video by its duration (2 minutes, checked by `video.probe`), not by size. A
# technical cap of the original only guards the server; PROPOSAL: 1 GB holds two minutes of 4K
# from a phone.
MAX_VIDEO_BYTES = 1024 * 1024 * 1024


def inspection_photo_type(content: bytes) -> str:
    """§ 10.4.2: JPEG, PNG and WebP up to 5 MB."""
    if not content:
        raise errors.validation_error("file", code="FIELD_REQUIRED")
    if len(content) > MAX_PHOTO_BYTES:
        raise too_large(MAX_PHOTO_BYTES)
    for content_type, matches in IMAGE_SIGNATURES.items():
        if matches(content[:16]):
            return content_type
    raise errors.validation_error("file", code="FILE_TYPE_NOT_ALLOWED")


def inspection_video_type(head: bytes) -> str:
    """§ 10.4.2: MP4 and MOV, told by the brand of their `ftyp` box (the first bytes)."""
    if not head:
        raise errors.validation_error("file", code="FIELD_REQUIRED")
    if head[4:8] != b"ftyp":
        raise errors.validation_error("file", code="FILE_TYPE_NOT_ALLOWED")
    return "video/quicktime" if head[8:10] == b"qt" else "video/mp4"


DOCUMENT_SIGNATURE = "document_signature"


def signature_type(content: bytes) -> str:
    """A signature drawn on the screen is a PNG image (§ 10.2.2)."""
    found = image_type(content)
    if found != "image/png":
        raise errors.validation_error("file", code="FILE_TYPE_NOT_ALLOWED")
    return found


def store(*, purpose: str, uploaded_by: UUID, content: bytes, content_type: str) -> StoredFile:
    """Writes the content, then the details, in the state «checking» (§ 24.4.4); the caller
    finishes the check (`checked`). Call inside the command's transaction: if it is rolled back,
    an unreferenced file may remain on disk, but no record points to it."""
    item = StoredFile(
        purpose=purpose,
        uploaded_by=uploaded_by,
        content_type=content_type,
        size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        state=StoredFile.State.CHECKING,
    )
    _root().mkdir(parents=True, exist_ok=True)
    _path(item.id).write_bytes(content)
    item.save(force_insert=True)
    return item


def checked(item: StoredFile, *, state: str = StoredFile.State.READY) -> StoredFile:
    """The check passed: the file becomes available by its purpose's rules (§ 24.4.4)."""
    item.state = state
    item.checked_at = timezone.now()
    item.save(update_fields=["state", "checked_at"])
    return item


def record_blocked(
    *,
    purpose: str,
    uploaded_by: UUID,
    session_id: UUID | None,
    content: bytes,
    content_type: str,
    reason: str,
    size: int | None = None,
    sha256: str | None = None,
) -> StoredFile:
    """§ 24.4.4: a dangerous or unsupported file is blocked and the event journaled. The content
    is not kept; the details of the attempt stay, so a new upload does not replace its history.
    Written in its own transaction: the upload itself is refused."""
    from c4rsys.platform.models import AuditEvent

    with transaction.atomic():
        item = StoredFile.objects.create(
            purpose=purpose,
            uploaded_by=uploaded_by,
            content_type=content_type[:100] or "application/octet-stream",
            size=max(size if size is not None else len(content), 1),
            sha256=sha256 or hashlib.sha256(content).hexdigest(),
            state=StoredFile.State.BLOCKED,
            blocked_reason=reason[:40],
            checked_at=timezone.now(),
        )
        AuditEvent.objects.create(
            actor_user_id=uploaded_by,
            session_id=session_id,
            action="file.blocked",
            object_type="stored_file",
            object_id=str(item.id),
            result="blocked",
            after={"purpose": purpose, "reason": reason, "size": item.size, "sha256": item.sha256},
        )
    return item


# States in which other users may read a file; the uploader also reads it while it is checked.
AVAILABLE = (StoredFile.State.READY, StoredFile.State.PROCESSING, StoredFile.State.FAILED)


def usable(file_id: UUID, *, purpose: str, user_id: UUID, company_id: UUID | None = None) -> None:
    """A file may be linked only by its uploader, for its purpose, and not to another company."""
    item = StoredFile.objects.filter(
        pk=file_id, purpose=purpose, uploaded_by=user_id, state__in=AVAILABLE
    ).first()
    if item is None or (item.company_id is not None and item.company_id != company_id):
        raise errors.validation_error("file", code="FILE_NOT_USABLE")


def attach_to_company(file_id: UUID, company_id: UUID) -> None:
    """Binds a logo to its company, so the company's readers may see it."""
    StoredFile.objects.filter(pk=file_id, company_id__isnull=True).update(company_id=company_id)


def readable(user_id: UUID, file_id: UUID) -> StoredFile | None:
    item = StoredFile.objects.filter(pk=file_id).exclude(state=StoredFile.State.BLOCKED).first()
    if item is None:
        return None
    if item.uploaded_by == user_id:
        return item
    if item.state not in AVAILABLE:
        return None
    reader = _readers.get(item.purpose)
    return item if reader is not None and reader(user_id, item) else None


def of_company(company_id: UUID, file_id: UUID) -> StoredFile | None:
    """A file of the company for a module that has checked the access itself (a signing link)."""
    return StoredFile.objects.filter(pk=file_id, company_id=company_id, state__in=AVAILABLE).first()


def content_path(item: StoredFile) -> tuple[Path, str]:
    """Where the content is and its type; a video may still be compressed (decision В-8.1)."""
    from c4rsys.files import video

    if item.removed_at is not None:
        raise errors.business_rule("FILE_REMOVED")
    if item.purpose == INSPECTION_VIDEO:
        return video.readable_path(item)
    return _path(item.id), item.content_type


def content(item: StoredFile) -> bytes:
    return content_path(item)[0].read_bytes()


# § 24.4.4: what a browser may show in place; everything else is only saved.
INLINE_TYPES = ("image/png", "image/jpeg", "image/webp", "video/mp4", "video/quicktime")
INLINE_DOCUMENTS = ("application/pdf",)
# A user file opened directly runs nothing: no scripts, no frames, no forms (PROPOSAL).
SANDBOX = "sandbox; default-src 'none'; img-src 'self'; media-src 'self'; style-src 'unsafe-inline'"


def safe_headers(
    response: HttpResponseBase, content_type: str, *, attachment: bool = False
) -> None:
    """Headers for the content of a user file (§ 24.4.4): the stored type only, never sniffed;
    active content is not run in the interface. Only checked types are ever stored, so no HTML,
    SVG or script is served from a user file."""
    inline = not attachment and content_type in (*INLINE_TYPES, *INLINE_DOCUMENTS)
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Disposition"] = "inline" if inline else "attachment"
    if content_type not in INLINE_DOCUMENTS:
        # The browser's PDF viewer does not work in a sandbox; a PDF with active content is
        # blocked by the check instead (files.checks).
        response["Content-Security-Policy"] = SANDBOX
    response["Cross-Origin-Opener-Policy"] = "same-origin"


def file_response(item: StoredFile, *, attachment: bool = False) -> HttpResponse:
    """The whole content of a checked file with the safe headers, not cached."""
    response = HttpResponse(content(item), content_type=item.content_type)
    response["Cache-Control"] = "private, no-store"
    safe_headers(response, item.content_type, attachment=attachment)
    return response


def remove(file_id: UUID) -> None:
    """Removes the content by its retention period; the details of the file stay. Call inside
    the caller's transaction: the content is deleted after it commits."""
    StoredFile.objects.filter(pk=file_id, removed_at__isnull=True).update(removed_at=timezone.now())
    paths = [_path(file_id), _root() / f"{file_id}.source"]

    def unlink() -> None:
        for path in paths:
            path.unlink(missing_ok=True)

    transaction.on_commit(unlink)
