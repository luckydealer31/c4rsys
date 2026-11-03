"""Background loops say they are alive (audit item 30). Every loop of docker-compose.prod.yml ends
its round with `manage.py heartbeat <name>`; /api/health/full tells a loop late when its last
round is older than its limit, and the outside check that watches that address writes to the
people on duty. The limits are a few rounds of each loop (PROPOSAL, агент, не подтверждено)."""

from datetime import datetime, timedelta

from django.utils import timezone

from c4rsys.platform.models import JobHeartbeat

# name → how long without a finished round counts as a stop. Rounds: rates every 3 hours,
# deposits every 10 minutes, schedule every 5 minutes.
LIMITS = {
    "rates": timedelta(hours=4),
    "deposits": timedelta(minutes=30),
    "schedule": timedelta(minutes=20),
    # Audit item 27, the systemd timers of deploy/backup-timer.sh on the server: the daily copy
    # (scripts/backup.sh) and the weekly restore of a copy (scripts/restore-check.sh).
    "backup": timedelta(hours=26),
    "restore_check": timedelta(days=8),
}


def beat(name: str, now: datetime | None = None) -> None:
    if name not in LIMITS:
        raise ValueError(f"Unknown background loop: {name}")
    JobHeartbeat.objects.update_or_create(name=name, defaults={"beat_at": now or timezone.now()})


def states(now: datetime | None = None) -> dict[str, str]:
    """«ok», «late» or «never» for every loop."""
    moment = now or timezone.now()
    last = dict(JobHeartbeat.objects.values_list("name", "beat_at"))
    result = {}
    for name, limit in LIMITS.items():
        at = last.get(name)
        result[name] = "never" if at is None else ("ok" if moment - at <= limit else "late")
    return result
