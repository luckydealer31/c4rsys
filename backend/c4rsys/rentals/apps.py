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
        from c4rsys.notifications import services as notifications
        from c4rsys.orders import services as orders
        from c4rsys.rentals import billing, extra_lines, notices, order_links, rentals, tariffs

        readiness.register("rental", tariffs.rental_conditions_met)
        locations.register_blockers(rentals.location_blockers)
        moves.register_busy(rentals.vehicle_busy)
        fleet.register_later_operations(rentals.started_after)
        fleet.register_context_blockers(rentals.vehicle_context_blockers)
        orders.register_operations(rentals.operations_for_order)
        orders.register_control_version(rentals.record_control_versions)
        # Decisions Р16, Р25: charged by the known period; estimated without a client.
        orders.register_estimated(billing.estimated_for_order)
        orders.register_on_client_assigned(billing.on_client_assigned)
        orders.register_history(rentals.history_for_order)
        # § 12.1.4, § 13.4.11: additional services are lines of the order of their own.
        orders.register_operations(extra_lines.operations_for_order)
        orders.register_history(extra_lines.history_for_order)
        # § 12.2.1–12.2.2: proposals become bookings; the cancelled order takes its positions.
        orders.register_on_formed(order_links.on_order_formed)
        orders.register_on_cancel(order_links.on_order_cancel)
        # § 20.3.5, § 14.3.7, decision З-11: notifications and system tasks of rentals.
        notifications.register_check(notices.run)
