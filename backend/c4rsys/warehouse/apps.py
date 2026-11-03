from django.apps import AppConfig


class WarehouseConfig(AppConfig):
    """Warehouse: catalog of items, storage, stock and its movements (ТЗ гл. 11)."""

    name = "c4rsys.warehouse"
    label = "warehouse"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        # § 2.8.2: the records of the module in the single search line.
        from c4rsys.platform import search as platform_search
        from c4rsys.warehouse import search as warehouse_search

        platform_search.register("item", warehouse_search.provider)
        from c4rsys.companies import readiness

        # TODO(spec): § 4.3.1 does not define readiness of a warehouse context. PROPOSAL: an
        # enabled warehouse context is ready — it always stores (§ 11.1.5).
        readiness.register("warehouse", lambda company_id, location_id: True)

        from c4rsys.companies import deactivation
        from c4rsys.warehouse import blockers

        # § 3.6.1: unfinished warehouse operations block deactivation of the company.
        deactivation.register_blocker(blockers.company_blockers)

        from c4rsys.orders import services as orders
        from c4rsys.warehouse import sales

        # § 11.9.1, § 11.9.4: goods lines are operations of the order; closing sells them and
        # needs the right of sale.
        orders.register_operations(sales.operations_for_order, sales.operations_for_orders)
        orders.register_client_view(sales.client_view_for_order)
        from c4rsys.orders import discounts

        discounts.register_goods_categories(sales.goods_categories)
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

        from c4rsys.notifications import services as notifications
        from c4rsys.warehouse import notices

        # § 20.3.5: expiry of lots (decision З-7).
        notifications.register_check(notices.run)
        # § 11.7.8: low stock to the chosen employees; § 11.7.1: reserves past their term.
        notifications.register_check(notices.low_stock)
        # § 20.3.4–20.3.5, § 19.1.9 (block 16 B): adjustments waiting for the confirmation and
        # shortages of moves.
        notifications.register_check(notices.confirmations)
        notifications.register_check(notices.discrepancies)
        notifications.register_check(reserves.expire)

        from c4rsys.fleet import odometer
        from c4rsys.warehouse import corrections, parts

        # § 11.4.3: the odometer of installations follows a change of the unit (§ 8.2.10).
        odometer.register_unit_participant("warehouse", parts.unit_values)
        # § 11.6.2: a write-off of a removed part is not corrected as a stock movement.
        corrections.register_line_guard(parts.guarded_lines)
