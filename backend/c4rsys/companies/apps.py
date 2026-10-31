from django.apps import AppConfig


class CompaniesConfig(AppConfig):
    """Company, ownership shares, positions and employment (ТЗ гл. 3, § 5.1)."""

    name = "c4rsys.companies"
    label = "companies"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from uuid import UUID

        from c4rsys.companies.models import Employment
        from c4rsys.files import services as files
        from c4rsys.files.models import StoredFile

        def logo_reader(user_id: UUID, item: StoredFile) -> bool:
            """A company logo is seen by the company's current employees."""
            return (
                item.company_id is not None
                and Employment.objects.filter(
                    company_id=item.company_id, user_id=user_id, state=Employment.State.ACTIVE
                ).exists()
            )

        def avatar_reader(user_id: UUID, item: StoredFile) -> bool:
            """§ 5.1.7: a photo the employee chose to give is seen by the company's current
            employees."""
            companies = Employment.objects.filter(
                user__avatar_id=item.id, shares_photo=True, state__in=("active", "blocked")
            ).values_list("company_id", flat=True)
            return Employment.objects.filter(
                company_id__in=companies, user_id=user_id, state=Employment.State.ACTIVE
            ).exists()

        # TODO(spec): § 3.5.3 puts the logo into the minimal search card, but the logo file is
        # still seen only by the company's employees (an earlier decision with its test).
        files.register_reader(StoredFile.Purpose.COMPANY_LOGO, logo_reader)
        files.register_reader(StoredFile.Purpose.AVATAR, avatar_reader)
        # A photo of a catalog item is seen like the logo: by the company's employees.
        files.register_reader(StoredFile.Purpose.ITEM_PHOTO, logo_reader)
        # PROPOSAL: a receipt document is seen by the company's employees, like the logo.
        files.register_reader(StoredFile.Purpose.WAREHOUSE_DOCUMENT, logo_reader)

        # § 3.6.1: no new activity in a deactivated company, checked inside every command.
        from c4rsys.companies import deactivation, ownership_notices
        from c4rsys.notifications import services as notifications
        from c4rsys.platform import commands

        commands.register_guard(deactivation.guard)
        # § 24.1.8: places of work, positions and shares move with the merge of accounts.
        from c4rsys.companies.userlinks import CompanyLinks
        from c4rsys.platform import userlinks

        userlinks.register(CompanyLinks())
        # § 2.8.1: an unavailable object on an address of the active company is journaled as a
        # security event of that company.
        from c4rsys.companies.services import request_scope
        from c4rsys.platform import security

        security.register_scope_resolver(request_scope)
        notifications.register_check(ownership_notices.run)
