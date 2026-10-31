from typing import Any
from uuid import UUID

from django.apps import AppConfig


class InspectionsConfig(AppConfig):
    """Inspections of vehicles and their damages (ТЗ гл. 10)."""

    name = "c4rsys.inspections"
    label = "inspections"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from c4rsys.companies import services as companies
        from c4rsys.files import services as files
        from c4rsys.inspections import templates

        # § 10.3.5: ready templates of a new company.
        companies.register_created(templates.create_starting)
        files.register_reader(files.INSPECTION_PHOTO, file_reader)
        files.register_reader(files.INSPECTION_VIDEO, file_reader)
        # § 8.6.5: inspections made after a merge of cards are assigned at its split.
        from c4rsys.fleet import merges
        from c4rsys.inspections import card_records

        merges.register_card_records(card_records.PROVIDER)


def file_reader(user_id: UUID, item: Any) -> bool:
    """A photo or video is read by whoever may see its inspection or its damage (§ 10.3.7)."""
    from c4rsys.companies.models import Employment
    from c4rsys.inspections import access
    from c4rsys.inspections.models import DamageFile, Inspection, InspectionFile
    from c4rsys.platform.commands import Actor

    if item.company_id is None:
        return False
    employment = Employment.objects.filter(
        company_id=item.company_id, user_id=user_id, state=Employment.State.ACTIVE
    ).first()
    if employment is None:
        return False
    actor = Actor(user_id=user_id, company_id=item.company_id, employment_id=employment.id)
    owners = list(
        InspectionFile.objects.filter(file_id=item.id).values_list("inspection_id", flat=True)
    )
    for damage_file in DamageFile.objects.select_related("damage").filter(file_id=item.id):
        owners.append(damage_file.damage.inspection_id)
    for inspection in Inspection.objects.select_related("vehicle_card").filter(pk__in=owners):
        if access.can_view(actor, inspection):
            return True
    return False
