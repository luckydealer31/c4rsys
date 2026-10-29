"""Periodic checks of the notification centre: deadlines and reminders of tasks, terms of
documents (ТЗ гл. 20). Run on a schedule every 5 minutes (decision З-5)."""

from typing import Any

from django.core.management.base import BaseCommand

from c4rsys.notifications import services


class Command(BaseCommand):
    help = "Run the periodic checks of the notification centre."

    def handle(self, *args: Any, **options: Any) -> None:
        changed = services.run_checks()
        self.stdout.write(f"Notifications changed: {changed}")
