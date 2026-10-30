"""Videos of inspections (ТЗ § 10.4.2, decision В-8.1).

* The phone uploads the original, MP4 or MOV, up to 2 minutes long. The duration is measured by
  ffprobe at the upload, before the command; a longer video is refused with an explanation —
  it is never trimmed (§ 10.4.2).
* The server compresses it with ffmpeg to at most Full HD (the long side up to 1920, the short
  side up to 1080, the aspect kept), H.264/AAC in MP4 with «faststart» for playing in a browser.
* Compressing takes up to minutes, so it never runs inside a command's transaction: the upload
  command stores the original and marks the file `processing`; after the commit a background
  thread of the same process compresses it, then a short transaction stores the result, marks
  the file `ready` and journals it. The original is deleted once the result is stored.
* A compression interrupted by a restart is taken again by `manage.py process_videos` (on the
  schedule) once its start is stale. A failed compression marks the file `failed` and keeps the
  original, which is then served as it is (PROPOSAL: nothing of an inspection is lost).
"""

import hashlib
import json
import logging
import os
import shutil
import subprocess
import threading
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, BinaryIO
from uuid import UUID

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from c4rsys.files.models import StoredFile
from c4rsys.platform import errors
from c4rsys.platform.models import AuditEvent

logger = logging.getLogger(__name__)

# § 10.4.2: «up to 2 minutes each».
MAX_DURATION_SECONDS = Decimal(120)
# Decision В-8.1: not above Full HD.
LONG_SIDE = 1920
SHORT_SIDE = 1080
# PROPOSAL: the parameters are to be tuned on real phone videos (§ 10.4.2); a CRF of 26 keeps
# scratches and dents distinguishable at a moderate size.
CRF = "26"
PRESET = "veryfast"
AUDIO_BITRATE = "128k"
PROBE_TIMEOUT = 60
COMPRESS_TIMEOUT = 20 * 60
# A compression started this long ago and still `processing` was interrupted.
STALE_AFTER = timedelta(minutes=30)
PROCESS = "video_compression"
CHUNK = 1024 * 1024


def _ffmpeg() -> str:
    return str(getattr(settings, "C4R_FFMPEG", "ffmpeg"))


def _ffprobe() -> str:
    return str(getattr(settings, "C4R_FFPROBE", "ffprobe"))


def _root() -> Path:
    return Path(settings.C4R_FILES_ROOT)


def final_path(file_id: UUID) -> Path:
    return _root() / str(file_id)


def source_path(file_id: UUID) -> Path:
    """The original until it is compressed."""
    return _root() / f"{file_id}.source"


def _work_path(file_id: UUID) -> Path:
    return _root() / f"{file_id}.compressing.mp4"


# --- At the upload -------------------------------------------------------------------------------


def probe(path: Path) -> Decimal:
    """The duration of a video in seconds; refuses what is not a video or is too long."""
    try:
        found = subprocess.run(  # noqa: S603 — fixed arguments, the path is ours
            [
                _ffprobe(),
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            timeout=PROBE_TIMEOUT,
            check=False,
        )
    except FileNotFoundError as exc:
        logger.error("ffprobe is not installed")
        raise errors.business_rule("VIDEO_PROCESSING_UNAVAILABLE", field="file") from exc
    except subprocess.TimeoutExpired as exc:
        raise errors.validation_error("file", code="FILE_TYPE_NOT_ALLOWED") from exc
    if found.returncode != 0:
        raise errors.validation_error("file", code="FILE_TYPE_NOT_ALLOWED")
    try:
        info: dict[str, Any] = json.loads(found.stdout or b"{}")
    except ValueError as exc:
        raise errors.validation_error("file", code="FILE_TYPE_NOT_ALLOWED") from exc
    streams = info.get("streams") or []
    if not any(s.get("codec_type") == "video" for s in streams):
        raise errors.validation_error("file", code="FILE_TYPE_NOT_ALLOWED")
    candidates = [info.get("format", {}).get("duration")] + [s.get("duration") for s in streams]
    durations = []
    for value in candidates:
        try:
            number = Decimal(str(value))
        except ArithmeticError:
            continue
        if number.is_finite() and number > 0:
            durations.append(number)
    if not durations:
        raise errors.validation_error("file", code="VIDEO_DURATION_UNKNOWN")
    duration = max(durations).quantize(Decimal("0.01"))
    if duration > MAX_DURATION_SECONDS:
        raise errors.validation_error(
            "file",
            code="VIDEO_TOO_LONG",
            max_seconds=int(MAX_DURATION_SECONDS),
            seconds=int(duration),
        )
    return duration


def digest(stream: BinaryIO) -> str:
    stream.seek(0)
    sha = hashlib.sha256()
    for chunk in iter(lambda: stream.read(CHUNK), b""):
        sha.update(chunk)
    stream.seek(0)
    return sha.hexdigest()


def store_original(
    *,
    purpose: str,
    uploaded_by: UUID,
    stream: BinaryIO,
    size: int,
    sha256: str,
    content_type: str,
    duration: Decimal,
) -> StoredFile:
    """Inside the upload command: the original is written aside and the file is `processing`;
    the compression starts after the commit."""
    item = StoredFile(
        purpose=purpose,
        uploaded_by=uploaded_by,
        content_type=content_type,
        size=size,
        sha256=sha256,
        state=StoredFile.State.PROCESSING,
        duration_seconds=duration,
    )
    _root().mkdir(parents=True, exist_ok=True)
    stream.seek(0)
    with source_path(item.id).open("wb") as target:
        shutil.copyfileobj(stream, target, CHUNK)
    item.save(force_insert=True)
    file_id = item.id
    transaction.on_commit(lambda: start(file_id))
    return item


def start(file_id: UUID) -> None:
    """Compression in a background thread; `C4R_VIDEO_COMPRESSION = "off"` leaves it to the
    scheduled command (tests)."""
    if getattr(settings, "C4R_VIDEO_COMPRESSION", "thread") == "off":
        return
    threading.Thread(target=_in_thread, args=(file_id,), daemon=True).start()


def _in_thread(file_id: UUID) -> None:
    try:
        compress(file_id)
    except Exception:
        logger.exception("Compression of video %s failed", file_id)
    finally:
        connection.close()


# --- Compression ---------------------------------------------------------------------------------


def _scale() -> str:
    """At most Full HD in either orientation, the aspect kept, even sizes for H.264."""
    long_, short = LONG_SIDE, SHORT_SIDE
    return (
        f"scale=w='if(gte(iw,ih),min({long_},iw),min({short},iw))'"
        f":h='if(gte(iw,ih),min({short},ih),min({long_},ih))'"
        ":force_original_aspect_ratio=decrease:force_divisible_by=2"
    )


def command(source: Path, target: Path) -> list[str]:
    return [
        _ffmpeg(),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-vf",
        _scale(),
        "-c:v",
        "libx264",
        "-preset",
        PRESET,
        "-crf",
        CRF,
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        AUDIO_BITRATE,
        "-movflags",
        "+faststart",
        "-sn",
        "-dn",
        str(target),
    ]


def _claim(file_id: UUID) -> bool:
    """Only one worker compresses a file; a stale claim is taken over."""
    now = timezone.now()
    claimed = (
        StoredFile.objects.filter(pk=file_id, state=StoredFile.State.PROCESSING)
        .filter(
            Q(processing_started_at__isnull=True) | Q(processing_started_at__lt=now - STALE_AFTER)
        )
        .update(processing_started_at=now)
    )
    return claimed == 1


def _journal(item: StoredFile, action: str, after: dict[str, Any]) -> None:
    AuditEvent.objects.create(
        company_id=item.company_id,
        system_process=PROCESS,
        action=action,
        object_type="stored_file",
        object_id=str(item.id),
        after=after,
    )


def compress(file_id: UUID) -> bool:
    """Compresses one claimed video; True when the result is stored."""
    if not _claim(file_id):
        return False
    source = source_path(file_id)
    work = _work_path(file_id)
    failure = ""
    try:
        done = subprocess.run(  # noqa: S603 — fixed arguments, the paths are ours
            command(source, work), capture_output=True, timeout=COMPRESS_TIMEOUT, check=False
        )
        if done.returncode != 0 or not work.exists() or work.stat().st_size == 0:
            failure = (done.stderr or b"").decode(errors="replace")[-500:] or "no output"
    except FileNotFoundError:
        failure = "ffmpeg is not installed"
    except subprocess.TimeoutExpired:
        failure = "timeout"
    if failure:
        work.unlink(missing_ok=True)
        logger.error("Compression of video %s failed: %s", file_id, failure)
        with transaction.atomic():
            item = StoredFile.objects.select_for_update().get(pk=file_id)
            item.state = StoredFile.State.FAILED
            item.save(update_fields=["state"])
            _journal(item, "file.video_compression_failed", {"state": item.state})
        return False
    with work.open("rb") as result:
        sha256 = digest(result)
    size = work.stat().st_size
    os.replace(work, final_path(file_id))
    with transaction.atomic():
        item = StoredFile.objects.select_for_update().get(pk=file_id)
        before = {"content_type": item.content_type, "size": item.size}
        item.state = StoredFile.State.READY
        item.content_type = "video/mp4"
        item.size = size
        item.sha256 = sha256
        item.processing_started_at = None
        item.save(
            update_fields=["state", "content_type", "size", "sha256", "processing_started_at"]
        )
        _journal(
            item,
            "file.video_compressed",
            {
                "state": item.state,
                "before": before,
                "content_type": item.content_type,
                "size": size,
            },
        )
        transaction.on_commit(lambda: source.unlink(missing_ok=True))
    return True


def pending() -> list[UUID]:
    """Videos whose compression never started or was interrupted."""
    stale = timezone.now() - STALE_AFTER
    return list(
        StoredFile.objects.filter(state=StoredFile.State.PROCESSING)
        .filter(Q(processing_started_at__isnull=True) | Q(processing_started_at__lt=stale))
        .order_by("created_at")
        .values_list("id", flat=True)
    )


def process_pending() -> int:
    return sum(1 for file_id in pending() if compress(file_id))


# --- Reading -------------------------------------------------------------------------------------


def readable_path(item: StoredFile) -> tuple[Path, str]:
    """The content to serve: the result; the original of a failed compression; nothing while
    it is being compressed."""
    if item.state == StoredFile.State.PROCESSING:
        raise errors.business_rule("FILE_PROCESSING")
    if item.state == StoredFile.State.FAILED:
        return source_path(item.id), item.content_type
    return final_path(item.id), item.content_type
