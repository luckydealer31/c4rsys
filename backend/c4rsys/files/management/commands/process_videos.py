"""Compression of inspection videos that was never started or was interrupted (decision В-8.1).
Run on the schedule together with the other periodic commands; an upload normally starts the
compression itself right after its commit."""

from typing import Any

from django.core.management.base import BaseCommand

from c4rsys.files import video


class Command(BaseCommand):
    help = "Compress inspection videos left in processing."

    def handle(self, *args: Any, **options: Any) -> None:
        done = video.process_pending()
        self.stdout.write(f"Videos compressed: {done}")
