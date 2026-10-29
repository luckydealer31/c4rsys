"""Starting kinds of documents (§ 18.1.11), links inside the company and the rights of documents
(§ 23.8.1; customer's decision Р-6 of 29.10.2026): all for «Company administrator»; viewing,
uploading and creating by templates for «Rental manager» and «Service manager». Existing
assignments keep their own copy of codes (§ 23.1.3)."""

from typing import Any

from django.db import migrations

from c4rsys.platform.ids import uuid7

KINDS = ["contract", "act", "settlement", "purchase", "internal"]
CODES = [
    "documents.view",
    "documents.upload",
    "documents.create",
    "documents.templates",
    "documents.archive",
    "documents.sign",
]
MANAGER = ["documents.view", "documents.upload", "documents.create"]
SETS = {"company_admin": CODES, "rental_manager": MANAGER, "service_manager": MANAGER}

LINKS = """
ALTER TABLE document_location
    ADD CONSTRAINT document_location_document_same_company_fk
    FOREIGN KEY (company_id, document_id) REFERENCES document (company_id, id),
    ADD CONSTRAINT document_location_location_same_company_fk
    FOREIGN KEY (company_id, location_id) REFERENCES location (company_id, id);
ALTER TABLE document_version
    ADD CONSTRAINT document_version_document_same_company_fk
    FOREIGN KEY (company_id, document_id) REFERENCES document (company_id, id);
ALTER TABLE document_link
    ADD CONSTRAINT document_link_document_same_company_fk
    FOREIGN KEY (company_id, document_id) REFERENCES document (company_id, id);
"""
UNLINK = """
ALTER TABLE document_link DROP CONSTRAINT document_link_document_same_company_fk;
ALTER TABLE document_version DROP CONSTRAINT document_version_document_same_company_fk;
ALTER TABLE document_location
    DROP CONSTRAINT document_location_location_same_company_fk,
    DROP CONSTRAINT document_location_document_same_company_fk;
"""

# A version is never changed (§ 18.3.5): only new ones are added.
FROZEN = """
CREATE FUNCTION document_version_frozen() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'document_version is append-only';
END $$;
CREATE TRIGGER document_version_no_update
    BEFORE UPDATE OR DELETE ON document_version
    FOR EACH ROW EXECUTE FUNCTION document_version_frozen();
"""
UNFROZEN = """
DROP TRIGGER document_version_no_update ON document_version;
DROP FUNCTION document_version_frozen();
"""


def add(apps: Any, schema_editor: Any) -> None:
    kind = apps.get_model("documents", "DocumentKind")
    for code in KINDS:
        kind.objects.get_or_create(code=code, company=None, defaults={"id": uuid7()})
    permission = apps.get_model("companies", "Permission")
    permission_set = apps.get_model("companies", "PermissionSet")
    item = apps.get_model("companies", "PermissionSetItem")
    for code in CODES:
        permission.objects.get_or_create(code=code, defaults={"section": "documents"})
    for set_code, codes in SETS.items():
        target = permission_set.objects.get(code=set_code, is_system=True)
        for code in codes:
            item.objects.get_or_create(permission_set=target, permission_id=code)


def remove(apps: Any, schema_editor: Any) -> None:
    apps.get_model("companies", "PermissionSetItem").objects.filter(
        permission_id__in=CODES
    ).delete()
    apps.get_model("companies", "Permission").objects.filter(code__in=CODES).delete()
    apps.get_model("documents", "DocumentKind").objects.filter(company__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0001_documents"),
        ("delivery", "0004_delivery_annulled"),
    ]

    operations = [
        migrations.RunSQL(LINKS, UNLINK),
        migrations.RunSQL(FROZEN, UNFROZEN),
        migrations.RunPython(add, remove),
    ]
