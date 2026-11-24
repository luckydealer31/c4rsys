import re
from typing import Any

from django.db import migrations

# «ТС» as a word, not inside another one (ПТС stays).
WORD = re.compile(r"(?<![А-Яа-яЁёA-Za-z/])ТС(?![А-Яа-яЁёA-Za-z])")


def rename(apps: Any, schema_editor: Any) -> None:
    """A vehicle is written «Т/С» in Russian (customer's decision of 25.11.2026): the starting
    templates of the system say so too. The templates of companies are their own texts and are
    not touched; made documents keep their versions."""
    templates = apps.get_model("documents", "DocumentTemplate")
    for template in templates.objects.filter(company__isnull=True, language="ru"):
        title = WORD.sub("Т/С", template.title)
        content = WORD.sub("Т/С", template.content)
        if (title, content) != (template.title, template.content):
            template.title = title
            template.content = content
            template.save(update_fields=["title", "content"])


class Migration(migrations.Migration):
    dependencies = [("documents", "0011_registration_number")]

    operations = [migrations.RunPython(rename, migrations.RunPython.noop)]
