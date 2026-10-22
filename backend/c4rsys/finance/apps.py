from django.apps import AppConfig


class FinanceConfig(AppConfig):
    """Money of the company: cash desks and bank accounts (ТЗ гл. 17)."""

    name = "c4rsys.finance"
    label = "finance"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from c4rsys.finance import charges, payments
        from c4rsys.orders import services as orders

        orders.register_charged(charges.charged_for_order)
        orders.register_paid(payments.paid_for_order)
