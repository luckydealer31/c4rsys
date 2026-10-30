"""HTTP API of files: upload of an image and its reading."""

import hashlib
from uuid import UUID

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_GET, require_POST

from c4rsys.accounts.middleware import require_user
from c4rsys.files import services
from c4rsys.files.models import StoredFile
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult, run_command
from c4rsys.platform.http import idempotency_key, respond
from c4rsys.platform.middleware import get_request_id


@require_POST
def upload(request: HttpRequest) -> JsonResponse:
    """Multipart field `file`, query `purpose`. The file is linked later by the command that
    uses it (profile photo, company logo)."""
    user, session = require_user(request)
    purpose = request.GET.get("purpose", "")
    if purpose not in StoredFile.Purpose.values:
        raise errors.validation_error("purpose")
    uploaded = request.FILES.get("file")
    if uploaded is None:
        raise errors.validation_error("file", code="FIELD_REQUIRED")
    document = purpose in services.DOCUMENT_PURPOSES
    company_document = purpose in services.OFFICE_PURPOSES
    photo = purpose == services.INSPECTION_PHOTO
    video = purpose == services.INSPECTION_VIDEO
    limit = (
        services.MAX_PHOTO_BYTES
        if company_document or photo
        else services.MAX_VIDEO_BYTES
        if video
        else services.MAX_DOCUMENT_BYTES
        if document
        else services.MAX_IMAGE_BYTES
    )
    if uploaded.size is not None and uploaded.size > limit:
        raise errors.validation_error("file", code="FILE_TOO_LARGE", max_bytes=limit)
    data = uploaded.read()
    content_type = (
        services.company_document_type(data)
        if company_document
        else services.inspection_photo_type(data)
        if photo
        else services.inspection_video_type(data)
        if video
        else services.document_type(data)
        if document
        else services.image_type(data)
    )
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
        return CommandResult(201, {"file": {"id": str(item.id), "content_type": content_type}})

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


@require_GET
def download(request: HttpRequest, file_id: UUID) -> HttpResponse:
    user, _session = require_user(request)
    item = services.readable(user.id, file_id)
    if item is None:
        raise errors.not_found()
    response = HttpResponse(services.content(item), content_type=item.content_type)
    response["Cache-Control"] = "private, max-age=86400"
    response["Content-Disposition"] = "inline"
    return response
