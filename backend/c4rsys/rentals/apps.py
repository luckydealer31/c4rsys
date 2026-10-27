from django.apps import AppConfig


class RentalsConfig(AppConfig):
    """Rentals and tariffs (ТЗ гл. 13–14)."""

    name = "c4rsys.rentals"
    label = "rentals"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from c4rsys.companies import locations, readiness
        from c4rsys.fleet import moves
        from c4rsys.fleet import services as fleet
        from c4rsys.orders import services as orders
        from c4rsys.rentals import extra_lines, rentals, tariffs

        readiness.register("rental", tariffs.rental_conditions_met)
        locations.register_blockers(rentals.location_blockers)
        moves.register_busy(rentals.vehicle_busy)
        fleet.register_context_blockers(rentals.vehicle_context_blockers)
        orders.register_operations(rentals.operations_for_order)
        orders.register_control_version(rentals.record_control_versions)
        # § 23.6.5: closing an order with a rental needs the rental section right (22.10.2026).
        orders.register_section_permission("rental", "rental.complete_formalization")
        orders.register_history(rentals.history_for_order)
        # § 12.1.4, § 13.4.11: additional services are lines of the order of their own.
        orders.register_operations(extra_lines.operations_for_order)
        orders.register_section_permission("service", "order.close")
        orders.register_history(extra_lines.history_for_order)
