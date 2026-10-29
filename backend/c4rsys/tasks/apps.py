from django.apps import AppConfig


class TasksConfig(AppConfig):
    """Work tasks of a company (ТЗ гл. 19)."""

    name = "c4rsys.tasks"
    label = "tasks"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from c4rsys.files import services as files
        from c4rsys.notifications import services as notifications
        from c4rsys.tasks import notices, services

        # Decision З-10: an attachment is read by whoever may view its task.
        files.register_reader(files.TASK_ATTACHMENT, services.file_reader)
        # Decisions З-4, З-5: reminders, overdue notices, tasks of executors without access.
        notifications.register_check(notices.run)
