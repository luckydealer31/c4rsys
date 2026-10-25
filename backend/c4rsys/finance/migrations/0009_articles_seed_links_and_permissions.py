"""Finance stage, part 1 (ТЗ § 17.2.8, § 22.1.7, § 23.7.1, § 23.2.7).

Money records link an article, a location, a context and a vehicle card of the same company.
Existing companies get the starting articles. New finance permissions go to «Company
administrator» and «Accountant»; existing assignments keep their own copy of codes (§ 23.1.3).
"""

from typing import Any

from django.db import migrations
from django.utils import timezone

from c4rsys.platform.ids import uuid7

FORWARD = """
ALTER TABLE money_movement
    ADD CONSTRAINT money_movement_article_same_company_fk
    FOREIGN KEY (company_id, article_id) REFERENCES finance_article (company_id, id),
    ADD CONSTRAINT money_movement_location_same_company_fk
    FOREIGN KEY (company_id, location_id) REFERENCES location (company_id, id),
    ADD CONSTRAINT money_movement_context_same_company_fk
    FOREIGN KEY (company_id, context_id) REFERENCES activity_context (company_id, id),
    ADD CONSTRAINT money_movement_vehicle_same_company_fk
    FOREIGN KEY (company_id, vehicle_id) REFERENCES vehicle_card (company_id, id);
"""

REVERSE = """
ALTER TABLE money_movement
    DROP CONSTRAINT IF EXISTS money_movement_vehicle_same_company_fk,
    DROP CONSTRAINT IF EXISTS money_movement_context_same_company_fk,
    DROP CONSTRAINT IF EXISTS money_movement_location_same_company_fk,
    DROP CONSTRAINT IF EXISTS money_movement_article_same_company_fk;
"""

# Copy of articles.STARTING at the time of the migration.
STARTING = [
    ("income", "rental"),
    ("income", "service"),
    ("income", "goods_sale"),
    ("income", "reimbursement"),
    ("income", "other_income"),
    ("expense", "goods_materials"),
    ("expense", "vehicle_upkeep"),
    ("expense", "contractor_services"),
    ("expense", "owner_settlements"),
    ("expense", "staff"),
    ("expense", "administrative"),
    ("expense", "taxes_fees"),
    ("expense", "other_expense"),
]

CODES = [
    "finance.account.view",
    "finance.income.record",
    "finance.payout",
    "finance.article.manage",
]


def add(apps: Any, schema_editor: Any) -> None:
    article = apps.get_model("finance", "FinanceArticle")
    now = timezone.now()
    for company_id in apps.get_model("companies", "Company").objects.values_list("id", flat=True):
        for kind, code in STARTING:
            article.objects.get_or_create(
                company_id=company_id,
                code=code,
                defaults={"id": uuid7(), "kind": kind, "created_at": now},
            )

    permission = apps.get_model("companies", "Permission")
    permission_set = apps.get_model("companies", "PermissionSet")
    item = apps.get_model("companies", "PermissionSetItem")
    for code in CODES:
        permission.objects.get_or_create(code=code, defaults={"section": "finance"})
    for set_code in ("company_admin", "accountant"):
        target = permission_set.objects.get(code=set_code, is_system=True)
        for code in CODES:
            item.objects.get_or_create(permission_set=target, permission_id=code)


def remove(apps: Any, schema_editor: Any) -> None:
    apps.get_model("companies", "PermissionSetItem").objects.filter(
        permission_id__in=CODES
    ).delete()
    apps.get_model("companies", "Permission").objects.filter(code__in=CODES).delete()
    apps.get_model("finance", "FinanceArticle").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("finance", "0008_articles_and_manual_records"),
        ("companies", "0017_import_permissions"),
        ("fleet", "0010_vehicle_storage"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE),
        migrations.RunPython(add, remove),
    ]
