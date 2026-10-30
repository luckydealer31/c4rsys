from django.apps import AppConfig


class ExportsConfig(AppConfig):
    """The full export of the company's data (ТЗ § 22.6.1)."""

    name = "c4rsys.exports"
    label = "exports"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from c4rsys.exports import services
        from c4rsys.notifications import services as notifications

        # The 24 hours of a link (§ 2.8.1): the files of expired exports are removed on the
        # schedule of the periodic checks.
        notifications.register_check(services.purge_expired)
