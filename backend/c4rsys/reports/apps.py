from django.apps import AppConfig


class ReportsConfig(AppConfig):
    """Reports and dashboards (ТЗ гл. 21)."""

    name = "c4rsys.reports"
    label = "reports"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        # § 8.3.7: the income and the expenses of a managed vehicle passed to its owner.
        from c4rsys.fleet import disclosure
        from c4rsys.reports import vehicle_owner

        disclosure.register_provider("finance_income", vehicle_owner.income)
        disclosure.register_provider("finance_expenses", vehicle_owner.expenses)
