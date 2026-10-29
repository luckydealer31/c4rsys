from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    """The notification centre (ТЗ гл. 20)."""

    name = "c4rsys.notifications"
    label = "notifications"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from c4rsys.notifications import rules, services

        # Instant events of § 20.3.5 come from the journal of actions.
        services.register_check(rules.read_journal)
        # § 20.3.4: a change of an employee's access is told to the employee.
        rules.on_journal("employee.rights_changed", rules.on_access_changed)
        rules.on_journal("employee.access_blocked", rules.on_access_changed)
        rules.on_journal("employee.access_restored", rules.on_access_changed)
