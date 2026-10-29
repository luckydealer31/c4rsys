"""Makes the repetitions of repeating tasks whose date has come (ТЗ § 19.4.1). Run on a schedule
every 5 minutes (decision З-5)."""

from typing import Any

from django.core.management.base import BaseCommand

from c4rsys.tasks import series


class Command(BaseCommand):
    help = "Make the repetitions of repeating tasks whose date has come."

    def handle(self, *args: Any, **options: Any) -> None:
        made = series.run()
        self.stdout.write(f"Repetitions made: {made}")
