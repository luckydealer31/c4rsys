from django.apps import AppConfig


class PlatformConfig(AppConfig):
    """Shared technical foundations: errors, idempotency, audit journal, readable IDs, access."""

    name = "c4rsys.platform"
    label = "platform"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from django.core.signals import request_finished, request_started

        from c4rsys.platform import logs

        # Audit item 22: every log record of a request carries its ID.
        request_started.connect(logs.request_started, dispatch_uid="c4rsys.logs.started")
        request_finished.connect(logs.request_finished, dispatch_uid="c4rsys.logs.finished")
