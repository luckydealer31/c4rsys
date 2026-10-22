from django.apps import AppConfig


class FleetConfig(AppConfig):
    """Vehicles: global identity, company cards and odometer history (ТЗ гл. 8)."""

    name = "c4rsys.fleet"
    label = "fleet"
    default_auto_field = "django.db.models.BigAutoField"
