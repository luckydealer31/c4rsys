from django.apps import AppConfig


class OrdersConfig(AppConfig):
    """Orders: client, composition of operations, closing conditions (ТЗ гл. 12)."""

    name = "c4rsys.orders"
    label = "orders"
    default_auto_field = "django.db.models.BigAutoField"
