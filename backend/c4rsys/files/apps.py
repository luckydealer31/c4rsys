from django.apps import AppConfig


class FilesConfig(AppConfig):
    """Uploaded files: content on disk, details and links in the database (A10)."""

    name = "c4rsys.files"
    label = "files"
    default_auto_field = "django.db.models.BigAutoField"
