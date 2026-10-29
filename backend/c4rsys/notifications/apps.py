from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    """The notification centre (ТЗ гл. 20)."""

    name = "c4rsys.notifications"
    label = "notifications"
    default_auto_field = "django.db.models.BigAutoField"
