"""The outside check of the site and the background loops (audit item 30)."""

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.test import Client
from django.utils import timezone

from c4rsys.platform import heartbeat

pytestmark = pytest.mark.django_db


def _all_beat(minutes_ago: float = 0) -> None:
    for name in heartbeat.LIMITS:
        heartbeat.beat(name, timezone.now() - timedelta(minutes=minutes_ago))


def test_full_health_is_ok_while_every_loop_beats(client: Client) -> None:
    _all_beat()
    response = client.get("/api/health/full")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "database": "ok",
        "jobs": {
            "rates": "ok",
            "deposits": "ok",
            "schedule": "ok",
            "backup": "ok",
            "restore_check": "ok",
        },
    }


def test_a_late_or_missing_loop_answers_503(client: Client) -> None:
    _all_beat()
    heartbeat.beat("deposits", timezone.now() - timedelta(minutes=31))
    late = client.get("/api/health/full")
    assert late.status_code == 503
    assert late.json()["jobs"]["deposits"] == "late"

    # The plain health stays green: a late loop must not make Docker restart the backend.
    assert client.get("/api/health").status_code == 200


def test_never_beaten_counts_as_a_stop(client: Client) -> None:
    response = client.get("/api/health/full")
    assert response.status_code == 503
    assert set(response.json()["jobs"].values()) == {"never"}


def test_the_command_marks_a_round() -> None:
    call_command("heartbeat", "schedule")
    assert heartbeat.states()["schedule"] == "ok"
    with pytest.raises(ValueError):
        heartbeat.beat("unknown")
