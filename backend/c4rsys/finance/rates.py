"""Exchange rates (ТЗ § 17.1.3, § 17.1.5–17.1.8, § 23.7.6).

Reference rates come from ExchangeRate-API (decision of 26.10.2026) with USD as the base; a rate
between two other currencies is crossed through USD within one download. An operation gets the
last available rate as a suggestion; an employee with the right may set another rate by hand.
The applied rate is kept with the operation and never recalculated.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from django.db import transaction
from django.utils import timezone

from c4rsys.finance.models import ExchangeRate, RateFetch
from c4rsys.platform import errors
from c4rsys.platform.models import Currency
from c4rsys.platform.money import round_money

SOURCE = "exchangerate-api"
BASE = "USD"
URL = "https://v6.exchangerate-api.com/v6/{key}/latest/" + BASE
TIMEOUT_SECONDS = 20
RATE_PLACES = 12
# TODO(spec): when a rate counts as outdated is not set; the source updates once a day on the
# free plan, so a rate older than 36 hours is shown with a warning (PROPOSAL).
STALE_AFTER = timedelta(hours=36)


class FetchError(Exception):
    pass


def _quantize(rate: Decimal) -> Decimal:
    return rate.quantize(Decimal(1).scaleb(-RATE_PLACES), rounding=ROUND_HALF_UP)


def download(api_key: str) -> dict[str, Any]:
    """The answer of the source, or FetchError with its error type."""
    # The address is the constant https URL of the source; only the key is inserted.
    url = URL.format(key=urllib.parse.quote(api_key, safe=""))
    request = urllib.request.Request(url, headers={"Accept": "application/json"})  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            data: dict[str, Any] = json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise FetchError(type(exc).__name__) from exc
    if data.get("result") != "success":
        raise FetchError(str(data.get("error-type", "unknown")))
    return data


@transaction.atomic
def store(data: dict[str, Any]) -> RateFetch:
    """Keeps the rates of currencies known to c4rsys; unknown codes are skipped."""
    if data.get("base_code") != BASE:
        raise FetchError("unexpected-base")
    known = set(Currency.objects.values_list("code", flat=True))
    fetch = RateFetch.objects.create(
        source=SOURCE,
        base_id=BASE,
        source_time=datetime.fromtimestamp(int(data["time_last_update_unix"]), tz=UTC),
        fetched_at=timezone.now(),
    )
    rows = []
    for code, value in dict(data["conversion_rates"]).items():
        rate = Decimal(str(value))
        if code in known and rate > 0:
            rows.append(ExchangeRate(fetch=fetch, quote_id=code, rate=_quantize(rate)))
    ExchangeRate.objects.bulk_create(rows)
    return fetch


# --- Suggestion (§ 17.1.8) ----------------------------------------------------------------------


@dataclass(frozen=True)
class Suggestion:
    rate: Decimal
    fetch: RateFetch
    stale: bool


def _rate_in(fetch: RateFetch, code: str) -> Decimal | None:
    if code == fetch.base_id:
        return Decimal(1)
    row = ExchangeRate.objects.filter(fetch=fetch, quote_id=code).first()
    return row.rate if row else None


def suggest(source: str, target: str, at: datetime | None = None) -> Suggestion | None:
    """The last system rate available at `at` (now by default): 1 `source` = rate × `target`.
    None when there is no rate — then only a manual rate is possible, without a guess."""
    moment = at or timezone.now()
    for fetch in RateFetch.objects.filter(fetched_at__lte=moment).order_by("-fetched_at")[:5]:
        rate_source = _rate_in(fetch, source)
        rate_target = _rate_in(fetch, target)
        if rate_source and rate_target:
            return Suggestion(
                rate=_quantize(rate_target / rate_source),
                fetch=fetch,
                stale=moment - fetch.fetched_at > STALE_AFTER,
            )
    return None


def latest_fetch() -> RateFetch | None:
    return RateFetch.objects.order_by("-fetched_at").first()


# --- Applied rate of an operation (§ 17.1.3, § 17.1.8, § 23.7.6) -------------------------------


@dataclass(frozen=True)
class AppliedRate:
    rate: Decimal
    suggested: Decimal | None
    fetch: RateFetch | None
    manual: bool


def apply(source: str, target: str, manual: Decimal | None, *, may_override: bool) -> AppliedRate:
    """The rate to apply: the suggested one, or a manual one with the right to change it. Without
    the right and without a suggestion the operation is refused (it may be postponed)."""
    suggestion = suggest(source, target)
    suggested = suggestion.rate if suggestion else None
    fetch = suggestion.fetch if suggestion else None
    if manual is not None and manual != suggested:
        if not may_override:
            raise errors.permission_denied("finance.rate.override")
        return AppliedRate(rate=_quantize(manual), suggested=suggested, fetch=fetch, manual=True)
    if suggested is None:
        raise errors.business_rule("RATE_UNAVAILABLE", field="rate", source=source, target=target)
    return AppliedRate(rate=suggested, suggested=suggested, fetch=fetch, manual=False)


def convert(amount: Decimal, rate: Decimal, target: str) -> Decimal:
    """The amount in `target`, rounded to its minor units (§ 17.1.7)."""
    places = Currency.objects.get(code=target).minor_units
    return round_money(amount * rate, places)


def snapshot(applied: AppliedRate) -> dict[str, Any]:
    return {
        "applied_rate": applied.rate,
        "suggested_rate": applied.suggested,
        "rate_manual": applied.manual,
        "rate_source": applied.fetch.source if applied.fetch else None,
        "rate_fetched_at": applied.fetch.fetched_at.isoformat() if applied.fetch else None,
    }
