from django.apps import AppConfig


class CompaniesConfig(AppConfig):
    """Company, ownership shares, positions and employment (ТЗ гл. 3, § 5.1)."""

    name = "c4rsys.companies"
    label = "companies"
    default_auto_field = "django.db.models.BigAutoField"
