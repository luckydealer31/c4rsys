from django.apps import AppConfig


class FleetConfig(AppConfig):
    """Vehicles: global identity, company cards and odometer history (ТЗ гл. 8)."""

    name = "c4rsys.fleet"
    label = "fleet"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from c4rsys.companies import locations
        from c4rsys.fleet import services

        locations.register_blockers(services.location_blockers)
