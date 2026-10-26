"""Moves deposits whose hold term has passed to the clients' balances (ТЗ § 14.7.1). Run on a
schedule."""

from typing import Any

from django.core.management.base import BaseCommand

from c4rsys.finance import deposits


class Command(BaseCommand):
    help = "Move deposits with a passed hold term to the clients' balances."

    def handle(self, *args: Any, **options: Any) -> None:
        moved = deposits.release_expired()
        self.stdout.write(f"Deposits moved to balances: {moved}")
