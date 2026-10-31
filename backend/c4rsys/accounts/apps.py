from django.apps import AppConfig


class AccountsConfig(AppConfig):
    """Personal account, login contacts, one-time codes and server-side sessions (ТЗ гл. 2)."""

    name = "c4rsys.accounts"
    label = "accounts"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from c4rsys.accounts.services import ContactLinks
        from c4rsys.platform import userlinks

        # § 24.1.8: the login contacts move with the merge of accounts.
        userlinks.register(ContactLinks())
