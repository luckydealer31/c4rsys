from django.apps import AppConfig


class FinanceConfig(AppConfig):
    """Money of the company: cash desks and bank accounts (ТЗ гл. 17)."""

    name = "c4rsys.finance"
    label = "finance"
    default_auto_field = "django.db.models.BigAutoField"
