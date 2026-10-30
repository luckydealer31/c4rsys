from django.apps import AppConfig


class ExportsConfig(AppConfig):
    """The full export of the company's data (ТЗ § 22.6.1)."""

    name = "c4rsys.exports"
    label = "exports"
    default_auto_field = "django.db.models.BigAutoField"
