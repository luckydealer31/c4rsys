"""Starts and completes standalone time services without equipment by their period (ТЗ § 13.4.11;
customer's decision of 11.11.2026). Run on a schedule."""

from typing import Any

from django.core.management.base import BaseCommand

from c4rsys.rentals import extra_lines


class Command(BaseCommand):
    help = "Start and complete standalone time services whose period has come."

    def handle(self, *args: Any, **options: Any) -> None:
        started, completed = extra_lines.auto_progress()
        self.stdout.write(f"Services started by their period: {started}, completed: {completed}")
