"""The category of a counterparty becomes a tag (customer's decision of 28.11.2026): every filled
category is put on its card as a tag of the company with that name, and the field is emptied."""

from django.db import migrations

NAME_MAX = 40


def to_tags(apps, schema_editor):
    Counterparty = apps.get_model("parties", "Counterparty")
    Tag = apps.get_model("tags", "Tag")
    TagLink = apps.get_model("tags", "TagLink")
    for card in Counterparty.objects.exclude(category="").iterator():
        name = " ".join(card.category.split())[:NAME_MAX]
        if name:
            tag = Tag.objects.filter(
                company_id=card.company_id, owner_user_id__isnull=True, name_key=name.lower()
            ).first()
            if tag is None:
                tag = Tag.objects.create(
                    company_id=card.company_id,
                    name=name,
                    name_key=name.lower(),
                    color="gray",
                    created_by_user_id=card.created_by_user_id,
                )
            TagLink.objects.get_or_create(
                tag=tag,
                object_type=card.role,
                object_id=card.id,
                defaults={"company_id": card.company_id, "added_by_user_id": card.created_by_user_id},
            )
        card.category = ""
        card.save(update_fields=["category"])


class Migration(migrations.Migration):
    dependencies = [
        ("parties", "0019_party_papers"),
        ("tags", "0004_group_code"),
    ]

    operations = [migrations.RunPython(to_tags, migrations.RunPython.noop)]
