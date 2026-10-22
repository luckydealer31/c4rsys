from django.apps import AppConfig


class RentalsConfig(AppConfig):
    """Rentals and tariffs (ТЗ гл. 13–14)."""

    name = "c4rsys.rentals"
    label = "rentals"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from c4rsys.companies import readiness
        from c4rsys.orders import services as orders
        from c4rsys.rentals import rentals, tariffs

        readiness.register("rental", tariffs.rental_conditions_met)
        orders.register_operations(rentals.operations_for_order)
        orders.register_control_version(rentals.record_control_versions)
        # § 23.6.5: closing an order with a rental needs the rental section right (22.10.2026).
        orders.register_section_permission("rental", "rental.complete_formalization")
