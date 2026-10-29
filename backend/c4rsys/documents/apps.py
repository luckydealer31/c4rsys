from django.apps import AppConfig


class DocumentsConfig(AppConfig):
    """Documents, templates and signing (ТЗ гл. 18)."""

    name = "c4rsys.documents"
    label = "documents"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from c4rsys.documents import notices, services
        from c4rsys.files import services as files
        from c4rsys.files.models import StoredFile
        from c4rsys.notifications import services as notifications

        # § 18.5.3: a file of a document is read by the rights on the document, not by links.
        files.register_reader(StoredFile.Purpose.COMPANY_DOCUMENT, services.file_reader)
        # Decision Р-7: the warnings of terms go to the notification centre (part З3).
        notifications.register_check(notices.run)
