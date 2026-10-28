from django.apps import AppConfig


class WarehouseConfig(AppConfig):
    """Warehouse: catalog of items, storage, stock and its movements (ТЗ гл. 11)."""

    name = "c4rsys.warehouse"
    label = "warehouse"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from c4rsys.companies import readiness

        # TODO(spec): § 4.3.1 does not define readiness of a warehouse context. PROPOSAL: an
        # enabled warehouse context is ready — it always stores (§ 11.1.5).
        readiness.register("warehouse", lambda company_id, location_id: True)

        from c4rsys.orders import services as orders
        from c4rsys.warehouse import sales

        # § 11.9.1, § 11.9.4: goods lines are operations of the order; closing sells them and
        # needs the right of sale.
        orders.register_operations(sales.operations_for_order)
        orders.register_on_close(sales.on_order_close)
        orders.register_on_cancel(sales.on_order_cancel)

        from c4rsys.fleet import services as fleet
        from c4rsys.warehouse import reserves

        # § 8.1.5: a vehicle reserved in an open order does not leave the warehouse.
        fleet.register_garage_blockers(
            lambda company_id, card_id: (
                ["sale_reserve"] if reserves.vehicle_reserved(card_id) else []
            )
        )
