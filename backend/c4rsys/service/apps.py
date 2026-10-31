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
        from c4rsys.notifications import rules
        from c4rsys.notifications import services as notifications
        from c4rsys.orders import services as orders
        from c4rsys.service import catalog, notices, operations

        # § 4.3.1: a service context is ready with an active work in the catalog.
        readiness.register("service", catalog.has_active_work)
        locations.register_blockers(operations.location_blockers)
        from c4rsys.companies import deactivation

        deactivation.register_blocker(operations.company_blockers)
        # Decision С-3: an operation holds its vehicle for bookings, issues and moves.
        fleet.register_occupancy(operations.occupancy)
        moves.register_busy(operations.vehicle_busy)
        fleet.register_later_operations(operations.started_after)
        # § 8.6.5: operations made after a merge of cards are assigned at its split.
        from c4rsys.fleet import merges
        from c4rsys.service import card_records

        merges.register_card_records(card_records.PROVIDER)
        orders.register_operations(operations.operations_for_order)
        orders.register_client_view(operations.client_view_for_order)
        from c4rsys.fleet import services as fleet_services

        fleet_services.register_schedule("service", operations.schedule)
        orders.register_estimated(operations.estimated_for_order)
        orders.register_on_client_assigned(operations.on_client_assigned)
        orders.register_on_cancel(operations.on_order_cancel)
        orders.register_history(operations.history_for_order)
        # § 16.6.3: the cost of a contractor's work is owed to the counterparty.
        from c4rsys.finance import settlements

        settlements.register_obligations(operations.contractor_obligations)
        # § 20.3.5: maintenance due and works done in the notification centre.
        notifications.register_check(notices.run)
        rules.on_journal("service_operation.works_done", notices.on_works_done)
        # Chapter 10: inspections of a service operation (§ 10.1.1).
        from c4rsys.inspections import links as inspection_links

        inspection_links.register_service_reader(operations.inspection_link)
