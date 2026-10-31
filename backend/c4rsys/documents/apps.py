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
        from c4rsys.notifications import rules
        from c4rsys.notifications import services as notifications

        # § 18.5.3: a file of a document is read by the rights on the document, not by links.
        files.register_reader(StoredFile.Purpose.COMPANY_DOCUMENT, services.file_reader)
        # A signature drawn on the screen is read like the document it signs (§ 10.2.2).
        files.register_reader(StoredFile.Purpose.DOCUMENT_SIGNATURE, services.file_reader)
        # § 24.4.1: downloads of a document's files are journaled.
        files.register_read_hook(StoredFile.Purpose.COMPANY_DOCUMENT, services.journal_file_read)
        files.register_read_hook(StoredFile.Purpose.DOCUMENT_SIGNATURE, services.journal_file_read)
        # Decision Р-7: the warnings of terms go to the notification centre (part З3).
        notifications.register_check(notices.run)
        # § 20.3.4: the request and the result of signing are mandatory notifications.
        notifications.register_check(notices.signing)
        rules.on_journal("document.signed", notices.on_signing_answer)
        rules.on_journal("document.signing_declined", notices.on_signing_answer)
