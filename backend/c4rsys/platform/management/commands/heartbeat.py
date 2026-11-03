"""Marks a finished round of a background loop (audit item 30). Run at the end of every round."""

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from c4rsys.platform import heartbeat


class Command(BaseCommand):
    help = "Mark a finished round of a background loop."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("name", choices=sorted(heartbeat.LIMITS))

    def handle(self, *args: Any, **options: Any) -> None:
        heartbeat.beat(options["name"])
