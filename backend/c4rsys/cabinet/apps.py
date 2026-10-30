from django.apps import AppConfig


class CabinetConfig(AppConfig):
    """The personal cabinet of a profile on its own domain (ТЗ гл. 7, § 2.1.8). No tables."""

    name = "c4rsys.cabinet"
    label = "cabinet"
    default_auto_field = "django.db.models.BigAutoField"
