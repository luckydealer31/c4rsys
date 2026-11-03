"""Removal of old code sends (90 days) and of expired sign-in flows (30 days after expiry) (audit
item 48). Run on the schedule together with the other periodic commands."""

from typing import Any

from django.core.management.base import BaseCommand

from c4rsys.accounts import retention


class Command(BaseCommand):
    help = "Remove old code sends and expired sign-in flows."

    def handle(self, *args: Any, **options: Any) -> None:
        removed = retention.remove_old_records()
        self.stdout.write(f"Removed code sends: {removed['sends']}, flows: {removed['flows']}")
