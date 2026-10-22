from django.apps import AppConfig


class PartiesConfig(AppConfig):
    """Clients and counterparties: local cards of the company (ТЗ гл. 6)."""

    name = "c4rsys.parties"
    label = "parties"
    default_auto_field = "django.db.models.BigAutoField"
