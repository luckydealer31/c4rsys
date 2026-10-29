"""Personal dashboards (§ 21.2.4): the employee is of the same company."""

import c4rsys.platform.ids
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


LINKS = """
ALTER TABLE report_personal_dashboard
    ADD CONSTRAINT report_dashboard_employment_same_company_fk
    FOREIGN KEY (company_id, employment_id) REFERENCES employment (company_id, id);
"""
UNLINK = """
ALTER TABLE report_personal_dashboard
    DROP CONSTRAINT report_dashboard_employment_same_company_fk;
"""


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('companies', '0020_order_reopen_permission'),
        ('reports', '0001_report_permissions'),
    ]

    operations = [
        migrations.CreateModel(
            name='PersonalDashboard',
            fields=[
                ('id', models.UUIDField(default=c4rsys.platform.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=100)),
                ('metrics', models.JSONField(default=list)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('version', models.PositiveIntegerField(default=1)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='companies.company')),
                ('employment', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='companies.employment')),
            ],
            options={
                'db_table': 'report_personal_dashboard',
                'constraints': [models.CheckConstraint(condition=models.Q(('name', ''), _negated=True), name='report_dashboard_name_not_empty')],
            },
        ),
        migrations.RunSQL(LINKS, UNLINK),
    ]
