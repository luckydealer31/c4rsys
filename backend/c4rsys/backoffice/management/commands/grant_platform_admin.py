"""Makes an existing personal account the first platform administrator of the system staff
(ТЗ § 24.1.5). Run on the server once; the next members are added in the internal interface.

    python manage.py grant_platform_admin USR-XXXXXXXX [--unit "Platform"]

The grant is written to the system journal as an action of the process `bootstrap`.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from c4rsys.accounts.models import UserAccount
from c4rsys.backoffice import members
from c4rsys.platform.ids import normalize_public_id


class Command(BaseCommand):
    help = "Grant the platform administrator set of the system staff to an existing account."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("user", help="c4rsys ID of the account, USR-…")
        parser.add_argument("--unit", default="", help="Unit shown to companies (§ 24.3.2)")

    def handle(self, *args: Any, **options: Any) -> None:
        public_id = normalize_public_id(str(options["user"]))
        user = UserAccount.objects.filter(public_id=public_id).first() if public_id else None
        if user is None or user.state != UserAccount.State.ACTIVE:
            raise CommandError("No active account with this c4rsys ID")
        member = members.bootstrap_admin(user, str(options["unit"]))
        self.stdout.write(f"Platform administrator: {user.public_id} (member {member.id})")
