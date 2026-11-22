from django.apps import AppConfig


class TelegramConfig(AppConfig):
    """The Telegram bot of the system (customer's decisions of 22.11.2026): a quick way to work
    from a phone before the mobile applications. An employee binds the Telegram account to the
    profile, chooses a company and adds a vehicle by a photo of its paper."""

    name = "c4rsys.telegram"
    label = "telegram"
    default_auto_field = "django.db.models.BigAutoField"
