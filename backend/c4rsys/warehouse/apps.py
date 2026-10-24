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
