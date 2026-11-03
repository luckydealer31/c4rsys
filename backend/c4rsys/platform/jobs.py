"""Records of background jobs one by one (audit item 19). A record that fails is written to the log
with its error (and so to Sentry) and skipped; the job goes on with the next ones, and the next run
tries the failed one again. Each record keeps its own transaction inside `work`, so a failure rolls
back only that record."""

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)


def isolated(job: str, record: object, work: Callable[[], int]) -> int:
    """Runs `work` for one record; returns what it returns, or 0 when it fails."""
    try:
        return work()
    except Exception:
        logger.exception("Background job %s failed on %s; the others go on", job, record)
        return 0
