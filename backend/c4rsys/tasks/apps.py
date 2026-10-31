from django.apps import AppConfig


class TasksConfig(AppConfig):
    """Work tasks of a company (ТЗ гл. 19)."""

    name = "c4rsys.tasks"
    label = "tasks"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        # § 2.8.2: the records of the module in the single search line.
        from c4rsys.platform import search as platform_search
        from c4rsys.tasks import search as tasks_search

        platform_search.register("task", tasks_search.provider)
        from c4rsys.files import services as files
        from c4rsys.notifications import services as notifications
        from c4rsys.tasks import notices, services

        # Decision З-10: an attachment is read by whoever may view its task.
        files.register_reader(files.TASK_ATTACHMENT, services.file_reader)
        # Decisions З-4, З-5: reminders, overdue notices, tasks of executors without access.
        notifications.register_check(notices.run)

        from c4rsys.companies import deactivation
        from c4rsys.tasks import series

        # § 3.6.4: repetitions are paused while the company is deactivated.
        deactivation.register_restored(series.resume_after_restore)
        from c4rsys.platform import suspension

        # § 24.2.3: after a system block the missed repetitions are not made.
        suspension.register_resumed(series.resume_after_unblock)
