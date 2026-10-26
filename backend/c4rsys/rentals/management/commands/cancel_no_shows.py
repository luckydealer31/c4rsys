"""Cancels bookings whose auto-cancel moment at no-show has passed (ТЗ § 14.8.1). Run on a
schedule."""

from typing import Any

from django.core.management.base import BaseCommand

from c4rsys.rentals import bookings


class Command(BaseCommand):
    help = "Cancel bookings whose auto-cancel moment has passed."

    def handle(self, *args: Any, **options: Any) -> None:
        cancelled = bookings.cancel_no_shows()
        self.stdout.write(f"Bookings cancelled at no-show: {cancelled}")
