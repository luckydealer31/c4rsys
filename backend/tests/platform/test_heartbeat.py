"""The outside check of the site and the background loops (audit item 30)."""

import shutil
from datetime import timedelta
from unittest import mock

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
    with mock.patch("shutil.disk_usage", return_value=shutil._ntuple_diskusage(100, 50, 50)):
        response = client.get("/api/health/full")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "database": "ok",
        "disk": "ok",
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


def test_a_full_disk_answers_503(client: Client) -> None:
    """Audit item 50: the disk filled above C4R_DISK_ALERT_PERCENT (80 by default)."""
    _all_beat()
    usage = shutil._ntuple_diskusage
    with mock.patch("shutil.disk_usage", return_value=usage(100, 80, 20)):
        assert client.get("/api/health/full").json()["disk"] == "ok"
    with mock.patch("shutil.disk_usage", return_value=usage(100, 81, 19)):
        full = client.get("/api/health/full")
    assert full.status_code == 503
    assert full.json()["disk"] == "full"
    with mock.patch("shutil.disk_usage", side_effect=OSError):
        assert client.get("/api/health/full").json()["disk"] == "unavailable"
    # Docker's check does not look at the disk: a full disk must not restart the backend.
    with mock.patch("shutil.disk_usage", return_value=usage(100, 99, 1)):
        assert client.get("/api/health").status_code == 200
