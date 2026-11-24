from typing import Any

from django.db import migrations

# The forms of the word met in the starting templates, each with its replacement.
WORDS = [("госномером", "регистрационным номером"), ("госномер", "регистрационный номер")]


def rename(apps: Any, schema_editor: Any) -> None:
    """The plate of a vehicle is called «регистрационный номер» in Russian (customer's remark of
    24.11.2026): the starting templates of the system say so too. The templates of companies are
    their own texts and are not touched."""
    templates = apps.get_model("documents", "DocumentTemplate")
    for template in templates.objects.filter(company__isnull=True, language="ru"):
        content = template.content
        for old, new in WORDS:
            content = content.replace(old, new)
        if content != template.content:
            template.content = content
            template.save(update_fields=["content"])


class Migration(migrations.Migration):
    dependencies = [("documents", "0010_block16_scope_contexts_sources")]

    operations = [migrations.RunPython(rename, migrations.RunPython.noop)]
