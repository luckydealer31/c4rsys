import shutil

from django.conf import settings
from django.db import DatabaseError, connection
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET

from c4rsys.platform import heartbeat


@require_GET
def health(request: HttpRequest) -> JsonResponse:
    """Liveness and database check. Reads only; never performs a business operation."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError:
        return JsonResponse({"status": "error", "database": "unavailable"}, status=503)
    return JsonResponse({"status": "ok", "database": "ok"})


@require_GET
def health_full(request: HttpRequest) -> JsonResponse:
    """For an outside check (audit item 30): the site, the database and the background loops. A
    late loop answers 503 like a dead site, so one watched address covers both. The container
    keeps the plain /api/health: a late loop must not make Docker restart the backend."""
    try:
        jobs = heartbeat.states()
    except DatabaseError:
        return JsonResponse({"status": "error", "database": "unavailable"}, status=503)
    disk = disk_state()
    ok = all(state == "ok" for state in jobs.values()) and disk == "ok"
    return JsonResponse(
        {"status": "ok" if ok else "error", "database": "ok", "disk": disk, "jobs": jobs},
        status=200 if ok else 503,
    )


def disk_state() -> str:
    """Audit item 50: «full» when the disk of the uploaded files is filled above
    C4R_DISK_ALERT_PERCENT. On the test server the database, the copies and the images lie on the
    same disk, so this one check covers them all (deploy/README.md)."""
    try:
        usage = shutil.disk_usage(settings.C4R_FILES_ROOT)
    except OSError:
        return "unavailable"
    return "ok" if usage.used * 100 <= usage.total * settings.C4R_DISK_ALERT_PERCENT else "full"
