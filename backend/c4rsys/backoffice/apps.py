from django.apps import AppConfig


class BackofficeConfig(AppConfig):
    """The system staff of c4rsys and its internal interface (ТЗ гл. 24)."""

    name = "c4rsys.backoffice"
    label = "backoffice"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from c4rsys.accounts import services as accounts
        from c4rsys.backoffice import blocking, hooks, privileged
        from c4rsys.notifications import services as notifications
        from c4rsys.platform import commands, suspension

        # § 24.2.2: the other modules learn of the blocks through the platform.
        suspension.register_provider(blocking.Provider())
        # § 24.2.2: a blocked company runs no commands.
        commands.register_guard(hooks.company_guard)
        # § 24.2.2, § 24.1.1: a blocked user does not sign in; on the staff subdomain only an
        # active member of the system staff does.
        accounts.register_login_check(hooks.login_check)
        accounts.register_signed_in(hooks.signed_in)
        # Periodic: the end of blocks by their term, the end of privileged sessions after their
        # hour and the notices to the companies (run with the notification checks).
        notifications.register_check(blocking.run)
        notifications.register_check(privileged.run)
