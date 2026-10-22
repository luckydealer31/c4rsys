from django.apps import AppConfig


class AccountsConfig(AppConfig):
    """Personal account, login contacts, one-time codes and server-side sessions (ТЗ гл. 2)."""

    name = "c4rsys.accounts"
    label = "accounts"
    default_auto_field = "django.db.models.BigAutoField"
