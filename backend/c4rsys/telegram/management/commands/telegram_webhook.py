from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from c4rsys.telegram import api, bot


class Command(BaseCommand):
    help = "Tells Telegram where to send the updates of the bot (run once after the token is set)."

    def handle(self, *args: Any, **options: Any) -> None:
        if not api.configured():
            raise CommandError("TELEGRAM_BOT_TOKEN is not set")
        url = f"{settings.C4R_PUBLIC_URL.rstrip('/')}/api/telegram/webhook"
        api.call(
            "setWebhook",
            url=url,
            secret_token=api.secret(),
            allowed_updates=["message", "callback_query"],
        )
        self.stdout.write(f"The updates of @{api.bot_name()} go to {url}")
        # The button beside the field of the message opens the window of the bot (customer's
        # decision of 23.11.2026).
        page = bot.app_url()
        if page:
            api.call(
                "setChatMenuButton",
                menu_button={"type": "web_app", "text": "c4rsys", "web_app": {"url": page}},
            )
            self.stdout.write(f"The menu button opens {page}")
