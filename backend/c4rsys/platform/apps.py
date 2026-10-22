from django.apps import AppConfig


class PlatformConfig(AppConfig):
    """Shared technical foundations: errors, idempotency, audit journal, readable IDs, access."""

    name = "c4rsys.platform"
    label = "platform"
    default_auto_field = "django.db.models.BigAutoField"
