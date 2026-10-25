from django.apps import AppConfig


class FinanceConfig(AppConfig):
    """Money of the company: accounts, articles, accruals and money records (ТЗ гл. 17)."""

    name = "c4rsys.finance"
    label = "finance"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from c4rsys.companies import services as companies
        from c4rsys.finance import articles, charges, payments
        from c4rsys.orders import services as orders

        companies.register_created(articles.create_starting)
        orders.register_charged(charges.charged_for_order)
        orders.register_paid(payments.paid_for_order)
        orders.register_history(payments.history_for_order)
