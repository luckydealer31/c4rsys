"""Removal of stored results of commands older than 30 days (audit item 48). Run on the schedule
together with the other periodic commands."""

from typing import Any

from django.core.management.base import BaseCommand

from c4rsys.platform import commands


class Command(BaseCommand):
    help = "Remove the stored results of commands kept for 30 days."

    def handle(self, *args: Any, **options: Any) -> None:
        removed = commands.remove_old_idempotency_records()
        self.stdout.write(f"Removed stored results: {removed}")
