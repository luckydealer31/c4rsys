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

        files.register_reader(StoredFile.Purpose.COMPANY_LOGO, logo_reader)
