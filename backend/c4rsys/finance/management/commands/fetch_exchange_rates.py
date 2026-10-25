"""Downloads reference exchange rates (ТЗ § 17.1.6). Run on a schedule; the key of
ExchangeRate-API comes from C4R_EXCHANGE_RATE_API_KEY."""

import os
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from c4rsys.finance import rates


class Command(BaseCommand):
    help = "Download reference exchange rates from ExchangeRate-API."

    def handle(self, *args: Any, **options: Any) -> None:
        api_key = os.environ.get("C4R_EXCHANGE_RATE_API_KEY", "")
        if not api_key:
            raise CommandError("C4R_EXCHANGE_RATE_API_KEY is not set")
        try:
            fetch = rates.store(rates.download(api_key))
        except rates.FetchError as exc:
            # The last rates stay; operations show their age and a warning (§ 17.1.8).
            raise CommandError(f"Exchange rates were not downloaded: {exc}") from exc
        self.stdout.write(f"Exchange rates of {fetch.source_time:%Y-%m-%d %H:%M} UTC stored")
