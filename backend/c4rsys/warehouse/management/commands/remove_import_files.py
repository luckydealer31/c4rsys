"""Removal of import source files six months after the first upload (ТЗ § 22.2.4). Run on the
schedule together with the other periodic commands."""

from typing import Any

from django.core.management.base import BaseCommand

from c4rsys.warehouse import imports


class Command(BaseCommand):
    help = "Remove the files of imports kept for six months."

    def handle(self, *args: Any, **options: Any) -> None:
        removed = imports.remove_old_files()
        self.stdout.write(f"Imports with removed files: {removed}")
