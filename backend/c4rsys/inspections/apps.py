from django.apps import AppConfig


class InspectionsConfig(AppConfig):
    """Inspections of vehicles (ТЗ гл. 10). Stage 1: issue and return in the minimal form."""

    name = "c4rsys.inspections"
    label = "inspections"
    default_auto_field = "django.db.models.BigAutoField"
