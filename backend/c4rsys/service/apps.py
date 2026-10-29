from django.apps import AppConfig


class ServiceConfig(AppConfig):
    """Service works: catalog, posts, service operations (ТЗ гл. 16)."""

    name = "c4rsys.service"
    label = "service"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from c4rsys.companies import locations, readiness
        from c4rsys.fleet import moves
        from c4rsys.fleet import services as fleet
        from c4rsys.orders import services as orders
        from c4rsys.service import catalog, operations

        # § 4.3.1: a service context is ready with an active work in the catalog.
        readiness.register("service", catalog.has_active_work)
        locations.register_blockers(operations.location_blockers)
        # Decision С-3: an operation holds its vehicle for bookings, issues and moves.
        fleet.register_occupancy(operations.occupancy)
        moves.register_busy(operations.vehicle_busy)
        orders.register_operations(operations.operations_for_order)
        orders.register_estimated(operations.estimated_for_order)
        orders.register_on_client_assigned(operations.on_client_assigned)
        orders.register_on_cancel(operations.on_order_cancel)
        orders.register_history(operations.history_for_order)
