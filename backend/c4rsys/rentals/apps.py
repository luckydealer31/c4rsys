from django.apps import AppConfig


class RentalsConfig(AppConfig):
    """Rentals and tariffs (ТЗ гл. 13–14)."""

    name = "c4rsys.rentals"
    label = "rentals"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        # § 17.3.1 (block 16): the location and the vehicle of each charge of a rental.
        from c4rsys.finance import attribution
        from c4rsys.rentals import finance_links

        attribution.register_source("rental_base", finance_links.rentals)
        attribution.register_source("rental_mileage", finance_links.rentals)
        attribution.register_source("rental_manual", finance_links.manual_charges)
        attribution.register_source("rental_service", finance_links.service_lines)
        # § 2.8.2: the records of the module in the single search line.
        from c4rsys.platform import search as platform_search
        from c4rsys.rentals import search as rentals_search

        platform_search.register("rental", rentals_search.provider)
        # § 22.1.6 (block 16 B): planned issues and returns for the conflicts of a schedule.
        from c4rsys.companies import schedules
        from c4rsys.rentals import schedule_points

        schedules.register_points(schedule_points.points)
        from c4rsys.companies import locations, readiness
        from c4rsys.fleet import moves
        from c4rsys.fleet import services as fleet
        from c4rsys.notifications import services as notifications
        from c4rsys.orders import services as orders
        from c4rsys.rentals import billing, extra_lines, notices, order_links, rentals, tariffs

        readiness.register("rental", tariffs.rental_conditions_met)
        locations.register_blockers(rentals.location_blockers)
        from c4rsys.companies import deactivation

        deactivation.register_blocker(rentals.company_blockers)
        moves.register_busy(rentals.vehicle_busy)
        fleet.register_later_operations(rentals.started_after)
        # § 8.6.5: rentals made after a merge of cards are assigned at its split.
        from c4rsys.fleet import merges
        from c4rsys.rentals import card_records

        merges.register_card_records(card_records.PROVIDER)
        # § 6.5.3, § 6.1.14, § 8.1.2: rentals and the client cards.
        from c4rsys.parties import history as client_history
        from c4rsys.rentals import client_cards

        orders.register_on_client_moved(client_cards.on_client_moved)
        client_history.register_order_vehicles(client_cards.order_vehicles)
        fleet.register_context_blockers(rentals.vehicle_context_blockers)
        orders.register_operations(rentals.operations_for_order)
        orders.register_client_view(rentals.client_view_for_order)
        from c4rsys.fleet import services as fleet_services

        fleet_services.register_schedule("rental", rentals.schedule)
        orders.register_control_version(rentals.record_control_versions)
        # Decisions Р16, Р25: charged by the known period; estimated without a client.
        orders.register_estimated(billing.estimated_for_order)
        orders.register_on_client_assigned(billing.on_client_assigned)
        orders.register_history(rentals.history_for_order)
        # § 12.1.4, § 13.4.11: additional services are lines of the order of their own.
        orders.register_operations(extra_lines.operations_for_order)
        orders.register_client_view(extra_lines.client_view_for_order)
        orders.register_history(extra_lines.history_for_order)
        # § 12.2.1–12.2.2: proposals become bookings; the cancelled order takes its positions.
        orders.register_on_formed(order_links.on_order_formed)
        orders.register_on_cancel(order_links.on_order_cancel)
        # § 20.3.5, § 14.3.7, decision З-11: notifications and system tasks of rentals.
        notifications.register_check(notices.run)
        # Chapter 10: inspections know of rentals through these readers and handlers.
        from c4rsys.inspections import links as inspection_links
        from c4rsys.inspections import policy as inspection_policy
        from c4rsys.rentals import issue_policies, returns

        inspection_links.register_rental_reader(rentals.inspection_link)
        inspection_links.register_on_saved(returns.on_inspection_saved)
        inspection_policy.register_rental_check(issue_policies.inspection_required)
        # § 24.2.3: an auto-cancel missed during a system block becomes a conflict and a task.
        from c4rsys.platform import suspension
        from c4rsys.rentals import bookings

        suspension.register_resumed(bookings.after_unblock)
        # Block 12, part Б: unfinished rentals stop an archive (§ 8.7.5); the mileage terms change
        # with the odometer unit (§ 8.2.10); the operation periods for the owner (§ 8.3.15).
        from c4rsys.fleet import disclosure, odometer
        from c4rsys.rentals import vehicle_links

        fleet.register_pending(vehicle_links.pending)
        odometer.register_unit_participant("rentals", vehicle_links.unit_values)
        disclosure.register_provider("operation_periods", vehicle_links.periods_for_owner)
