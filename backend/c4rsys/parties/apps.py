from django.apps import AppConfig


class PartiesConfig(AppConfig):
    """Clients and counterparties: local cards of the company (ТЗ гл. 6)."""

    name = "c4rsys.parties"
    label = "parties"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from c4rsys.notifications import rules
        from c4rsys.notifications import services as notifications
        from c4rsys.parties import notices

        # § 20.3.4: requests to link with a profile and the answers are mandatory notifications.
        notifications.register_check(notices.requests)
        rules.on_journal("profile_link.accepted", notices.on_answer)
        rules.on_journal("profile_link.declined", notices.on_answer)
