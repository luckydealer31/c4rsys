from django.apps import AppConfig


class FleetConfig(AppConfig):
    """Vehicles: global identity, company cards and odometer history (ТЗ гл. 8)."""

    name = "c4rsys.fleet"
    label = "fleet"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from c4rsys.companies import locations
        from c4rsys.fleet import moves, services
        from c4rsys.orders import services as orders

        locations.register_blockers(services.location_blockers)
        orders.register_operations(moves.operations_for_order)
        orders.register_history(moves.history_for_order)
        orders.register_on_cancel(moves.on_order_cancel)
