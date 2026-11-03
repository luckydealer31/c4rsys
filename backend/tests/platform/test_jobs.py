"""Background jobs go on past a record that fails (audit item 19)."""

import logging
import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

import pytest

from c4rsys.finance import deposits
from c4rsys.notifications import services as notifications
from c4rsys.platform import jobs

pytestmark = pytest.mark.django_db


def test_a_failure_is_logged_and_counts_nothing(caplog: pytest.LogCaptureFixture) -> None:
    def broken() -> int:
        raise RuntimeError("bad record")

    with caplog.at_level(logging.ERROR, logger="c4rsys.platform.jobs"):
        assert jobs.isolated("deposit_release", "R-1", broken) == 0
    assert "deposit_release" in caplog.text and "R-1" in caplog.text
    assert caplog.records[0].exc_info is not None
    assert jobs.isolated("deposit_release", "R-2", lambda: 1) == 1


def test_notification_checks_go_on_after_one_fails() -> None:
    def broken(moment: datetime) -> int:
        raise RuntimeError("check broke")

    with mock.patch.object(notifications, "_checks", [broken, lambda m: 2, lambda m: 3]):
        assert notifications.run_checks() == 5


def test_deposits_go_on_after_one_fails() -> None:
    first, second = (SimpleNamespace(id=uuid.uuid4(), company_id=uuid.uuid4()) for _ in range(2))
    rentals = SimpleNamespace(returned_with_deposit_term=lambda moment: [first, second])
    with (
        mock.patch.object(deposits, "_rentals", return_value=rentals),
        mock.patch.object(
            deposits, "_release_one", side_effect=[RuntimeError("bad rental"), 1]
        ) as release,
    ):
        assert deposits.release_expired() == 1
    assert [c.args[1] for c in release.call_args_list] == [first.id, second.id]
