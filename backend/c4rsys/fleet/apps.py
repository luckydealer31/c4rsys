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
        # § 20.3.4: requests to the owner of a vehicle and the answers are mandatory.
        from c4rsys.fleet import owner_notices
        from c4rsys.notifications import rules
        from c4rsys.notifications import services as notifications

        notifications.register_check(owner_notices.requests)
        # § 8.8.3, § 8.8.7: restrictions that began or ended meanwhile.
        from c4rsys.fleet import restrictions

        notifications.register_check(restrictions.run)
        # § 8.2.3: the files of the global documents are read by the owner.
        from c4rsys.files import services as files
        from c4rsys.fleet import vehicle_documents

        files.register_reader(vehicle_documents.PURPOSE, vehicle_documents.file_reader)
        # § 8.3.15: the readings of the odometer in the category «mileage and fuel».
        from c4rsys.fleet import disclosure

        disclosure.register_provider("mileage_fuel", disclosure.readings_for_owner)
        rules.on_journal("vehicle.owner_answered", owner_notices.on_answer)
        orders.register_operations(moves.operations_for_order)
        orders.register_history(moves.history_for_order)
        orders.register_on_cancel(moves.on_order_cancel)
        # § 24.1.8: the ownership of vehicles moves with the merge of accounts.
        from c4rsys.fleet.userlinks import VehicleLinks
        from c4rsys.platform import userlinks

        userlinks.register(VehicleLinks())
