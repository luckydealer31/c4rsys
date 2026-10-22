from django.apps import AppConfig


class RentalsConfig(AppConfig):
    """Rentals and tariffs (ТЗ гл. 13–14)."""

    name = "c4rsys.rentals"
    label = "rentals"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from c4rsys.companies import readiness
        from c4rsys.rentals import tariffs

        readiness.register("rental", tariffs.rental_conditions_met)
