"""HTTP API of files: upload and reading."""

from __future__ import annotations

import hashlib
import re
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

from django.core.files.uploadedfile import UploadedFile
from django.http import FileResponse, HttpRequest, HttpResponse, JsonResponse, StreamingHttpResponse
from django.views.decorators.http import require_GET, require_POST

from c4rsys.accounts.middleware import require_user
from c4rsys.files import services, video
from c4rsys.files.models import StoredFile
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult, run_command
from c4rsys.platform.http import idempotency_key, respond
from c4rsys.platform.middleware import get_request_id


def _limit(purpose: str) -> int:
    if purpose in services.OFFICE_PURPOSES or purpose == services.INSPECTION_PHOTO:
        return services.MAX_PHOTO_BYTES
    if purpose == services.INSPECTION_VIDEO:
        return services.MAX_VIDEO_BYTES
    if purpose in services.DOCUMENT_PURPOSES:
        return services.MAX_DOCUMENT_BYTES
    return services.MAX_IMAGE_BYTES


def _content_type(purpose: str, data: bytes) -> str:
    if purpose in services.OFFICE_PURPOSES:
        return services.company_document_type(data)
    if purpose == services.INSPECTION_PHOTO:
        return services.inspection_photo_type(data)
    if purpose in services.DOCUMENT_PURPOSES:
        return services.document_type(data)
    return services.image_type(data)


def _file_body(item: StoredFile) -> dict[str, object]:
    return {
        "id": str(item.id),
        "content_type": item.content_type,
        "state": item.state,
        "duration_seconds": str(item.duration_seconds)
        if item.duration_seconds is not None
        else None,
    }


@require_POST
def upload(request: HttpRequest) -> JsonResponse:
    """Multipart field `file`, query `purpose`. The file is linked later by the command that
    uses it (profile photo, company logo, inspection)."""
    user, session = require_user(request)
    purpose = request.GET.get("purpose", "")
    if purpose not in StoredFile.Purpose.values:
        raise errors.validation_error("purpose")
    uploaded = request.FILES.get("file")
    if uploaded is None:
        raise errors.validation_error("file", code="FIELD_REQUIRED")
    limit = _limit(purpose)
    if uploaded.size is not None and uploaded.size > limit:
        raise services.too_large(limit)
    if purpose == services.INSPECTION_VIDEO:
        return _upload_video(request, user.id, session.id, uploaded)
    data = uploaded.read()
    content_type = _content_type(purpose, data)
    payload = {"purpose": purpose, "sha256": hashlib.sha256(data).hexdigest()}

    def handler(ctx: CommandContext) -> CommandResult:
        item = services.store(
            purpose=purpose, uploaded_by=user.id, content=data, content_type=content_type
        )
        ctx.audit(
            "file.uploaded",
            object_type="stored_file",
            object_id=item.id,
            after={"purpose": purpose, "content_type": content_type, "size": item.size},
        )
        return CommandResult(201, {"file": _file_body(item)})

    return respond(
        run_command(
            command="file.upload",
            actor=Actor(user_id=user.id, session_id=session.id),
            idempotency_key=idempotency_key(request),
            payload=payload,
            handler=handler,
            request_id=get_request_id(request),
        )
    )


def _upload_video(
    request: HttpRequest, user_id: UUID, session_id: UUID, uploaded: UploadedFile[bytes]
) -> JsonResponse:
    """Decision В-8.1: the type and the duration are checked before the command; the original is
    kept until the server compresses it, outside the command's transaction (`video`)."""
    stream: BinaryIO = uploaded.file  # type: ignore[assignment]
    stream.seek(0)
    content_type = services.inspection_video_type(stream.read(16))
    size = uploaded.size or 0
    if size <= 0:
        raise errors.validation_error("file", code="FIELD_REQUIRED")
    path = getattr(uploaded, "temporary_file_path", None)
    if path is not None:
        duration = video.probe(path())
    else:
        # A small upload stays in memory; ffprobe reads a temporary copy.
        with tempfile.NamedTemporaryFile(suffix=".video") as copy:
            stream.seek(0)
            copy.write(stream.read())
            copy.flush()
            duration = video.probe(Path(copy.name))
    sha256 = video.digest(stream)
    purpose = services.INSPECTION_VIDEO
    payload = {"purpose": purpose, "sha256": sha256}

    def handler(ctx: CommandContext) -> CommandResult:
        item = video.store_original(
            purpose=purpose,
            uploaded_by=user_id,
            stream=stream,
            size=size,
            sha256=sha256,
            content_type=content_type,
            duration=duration,
        )
        ctx.audit(
            "file.uploaded",
            object_type="stored_file",
            object_id=item.id,
            after={
                "purpose": purpose,
                "content_type": content_type,
                "size": size,
                "duration_seconds": duration,
                "state": item.state,
            },
        )
        return CommandResult(201, {"file": _file_body(item)})

    return respond(
        run_command(
            command="file.upload",
            actor=Actor(user_id=user_id, session_id=session_id),
            idempotency_key=idempotency_key(request),
            payload=payload,
            handler=handler,
            request_id=get_request_id(request),
        )
    )


RANGE = re.compile(r"bytes=(\d*)-(\d*)$")


def _chunks(handle: BinaryIO, length: int) -> Iterator[bytes]:
    with handle:
        while length > 0:
            chunk = handle.read(min(video.CHUNK, length))
            if not chunk:
                break
            length -= len(chunk)
            yield chunk


@require_GET
def download(request: HttpRequest, file_id: UUID) -> HttpResponse | StreamingHttpResponse:
    """The content inline; a byte range for playing a video in a phone's browser."""
    user, _session = require_user(request)
    item = services.readable(user.id, file_id)
    if item is None:
        raise errors.not_found()
    path, content_type = services.content_path(item)
    size = path.stat().st_size
    match = RANGE.match(request.headers.get("Range", "").strip())
    response: HttpResponse | StreamingHttpResponse
    if match and (match.group(1) or match.group(2)):
        first, last = match.group(1), match.group(2)
        if first:
            start = int(first)
            end = min(int(last), size - 1) if last else size - 1
        else:
            start = max(size - int(last), 0)
            end = size - 1
        if start > end or start >= size:
            response = HttpResponse(status=416)
            response["Content-Range"] = f"bytes */{size}"
            return response
        handle = path.open("rb")
        handle.seek(start)
        response = StreamingHttpResponse(
            _chunks(handle, end - start + 1), status=206, content_type=content_type
        )
        response["Content-Range"] = f"bytes {start}-{end}/{size}"
        response["Content-Length"] = str(end - start + 1)
    elif item.purpose == services.INSPECTION_VIDEO:
        response = FileResponse(path.open("rb"), content_type=content_type)
        response["Content-Length"] = str(size)
    else:
        response = HttpResponse(path.read_bytes(), content_type=content_type)
    response["Accept-Ranges"] = "bytes"
    response["Cache-Control"] = "private, max-age=86400"
    response["Content-Disposition"] = "inline"
    return response


@require_GET
def details(request: HttpRequest, file_id: UUID) -> JsonResponse:
    """The state of a file, so a page may wait for a video to be compressed."""
    user, _session = require_user(request)
    item = services.readable(user.id, file_id)
    if item is None:
        raise errors.not_found()
    return JsonResponse({"file": _file_body(item)})
