from django.apps import AppConfig


class DeliveryConfig(AppConfig):
    """Delivery and internal move of vehicles (ТЗ гл. 15)."""

    name = "c4rsys.delivery"
    label = "delivery"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from c4rsys.companies import locations
        from c4rsys.delivery import money, services
        from c4rsys.fleet import moves
        from c4rsys.fleet import services as fleet
        from c4rsys.orders import services as orders

        orders.register_operations(services.operations_for_order)
        orders.register_history(services.history_for_order)
        orders.register_on_cancel(services.on_order_cancel)
        # § 15.2.2 (decision Д-4): a client delivery is charged; estimated without a client.
        orders.register_on_client_assigned(money.on_client_assigned)
        orders.register_estimated(money.estimated_for_order)
        locations.register_blockers(services.location_blockers)
        # Decision Д-3: a vehicle in transit is held for issues, moves and the service.
        moves.register_busy(services.vehicle_busy)
        fleet.register_occupancy(services.occupancy)
        fleet.register_schedule("delivery", services.schedule)
        fleet.register_later_operations(services.started_after)
