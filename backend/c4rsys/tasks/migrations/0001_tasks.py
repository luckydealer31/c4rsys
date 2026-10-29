"""Work tasks (ТЗ гл. 19, § 23.9; customer's decisions З-1…З-11 of 29.10.2026): tasks, links,
comments and the company's policy; rows bound to the same company; the rights of tasks in the
system sets (decision З-3). Existing assignments keep their own copy of codes (§ 23.1.3)."""

from typing import Any

import c4rsys.platform.ids
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


CODES = ["tasks.own", "tasks.queue", "tasks.create", "tasks.view_all", "tasks.manage"]
BASIC = ["tasks.own", "tasks.queue", "tasks.create"]
SETS = {
    "company_admin": CODES,
    "rental_manager": CODES,
    "service_manager": CODES,
    "mechanic": BASIC,
    "storekeeper": BASIC,
    "accountant": BASIC,
    "driver": BASIC,
}

LINKS = """
ALTER TABLE task
    ADD CONSTRAINT task_location_same_company_fk
    FOREIGN KEY (company_id, location_id) REFERENCES location (company_id, id),
    ADD CONSTRAINT task_assignee_same_company_fk
    FOREIGN KEY (company_id, assignee_id) REFERENCES employment (company_id, id),
    ADD CONSTRAINT task_creator_same_company_fk
    FOREIGN KEY (company_id, created_by_employment_id) REFERENCES employment (company_id, id);
ALTER TABLE task_link
    ADD CONSTRAINT task_link_task_same_company_fk
    FOREIGN KEY (company_id, task_id) REFERENCES task (company_id, id);
ALTER TABLE task_comment
    ADD CONSTRAINT task_comment_task_same_company_fk
    FOREIGN KEY (company_id, task_id) REFERENCES task (company_id, id);
CREATE FUNCTION task_comment_frozen() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'task_comment is append-only';
END $$;
CREATE TRIGGER task_comment_no_change
    BEFORE UPDATE OR DELETE ON task_comment
    FOR EACH ROW EXECUTE FUNCTION task_comment_frozen();
"""
UNLINK = """
DROP TRIGGER task_comment_no_change ON task_comment;
DROP FUNCTION task_comment_frozen();
ALTER TABLE task_comment DROP CONSTRAINT task_comment_task_same_company_fk;
ALTER TABLE task_link DROP CONSTRAINT task_link_task_same_company_fk;
ALTER TABLE task
    DROP CONSTRAINT task_location_same_company_fk,
    DROP CONSTRAINT task_assignee_same_company_fk,
    DROP CONSTRAINT task_creator_same_company_fk;
"""


def add(apps: Any, schema_editor: Any) -> None:
    permission = apps.get_model("companies", "Permission")
    permission_set = apps.get_model("companies", "PermissionSet")
    item = apps.get_model("companies", "PermissionSetItem")
    for code in CODES:
        permission.objects.get_or_create(code=code, defaults={"section": "tasks"})
    for set_code, codes in SETS.items():
        target = permission_set.objects.get(code=set_code, is_system=True)
        for code in codes:
            item.objects.get_or_create(permission_set=target, permission_id=code)


def remove(apps: Any, schema_editor: Any) -> None:
    apps.get_model("companies", "PermissionSetItem").objects.filter(
        permission_id__in=CODES
    ).delete()
    apps.get_model("companies", "Permission").objects.filter(code__in=CODES).delete()


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('companies', '0020_order_reopen_permission'),
        ('files', '0005_task_attachment'),
    ]

    operations = [
        migrations.CreateModel(
            name='Task',
            fields=[
                ('id', models.UUIDField(default=c4rsys.platform.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('number', models.PositiveIntegerField()),
                ('title', models.CharField(max_length=300)),
                ('description', models.TextField(blank=True, default='')),
                ('priority', models.CharField(choices=[('low', 'Low'), ('normal', 'Normal'), ('high', 'High'), ('critical', 'Critical')], default='normal', max_length=10)),
                ('state', models.CharField(choices=[('open', 'Open'), ('in_progress', 'In Progress'), ('waiting', 'Waiting'), ('paused', 'Paused'), ('done', 'Done'), ('cancelled', 'Cancelled')], default='open', max_length=15)),
                ('state_note', models.CharField(blank=True, default='', max_length=1000)),
                ('source', models.CharField(choices=[('manual', 'Manual'), ('system', 'System')], default='manual', max_length=10)),
                ('system_event', models.CharField(blank=True, default='', max_length=60)),
                ('system_condition', models.CharField(blank=True, default='', max_length=500)),
                ('object_type', models.CharField(blank=True, default='', max_length=20)),
                ('object_id', models.UUIDField(blank=True, null=True)),
                ('due_at', models.DateTimeField(blank=True, null=True)),
                ('due_has_time', models.BooleanField(default=False)),
                ('created_by_user_id', models.UUIDField(blank=True, null=True)),
                ('closed_at', models.DateTimeField(blank=True, null=True)),
                ('closed_by_user_id', models.UUIDField(blank=True, null=True)),
                ('cancel_reason', models.CharField(blank=True, default='', max_length=1000)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('version', models.PositiveIntegerField(default=1)),
                ('assignee', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='companies.employment')),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='companies.company')),
                ('created_by_employment', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='companies.employment')),
                ('location', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='companies.location')),
            ],
            options={
                'db_table': 'task',
            },
        ),
        migrations.CreateModel(
            name='TaskComment',
            fields=[
                ('id', models.UUIDField(default=c4rsys.platform.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('text', models.TextField(blank=True, default='')),
                ('file_name', models.CharField(blank=True, default='', max_length=255)),
                ('author_user_id', models.UUIDField()),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='companies.company')),
                ('file', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='files.storedfile')),
                ('task', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='comments', to='tasks.task')),
            ],
            options={
                'db_table': 'task_comment',
            },
        ),
        migrations.CreateModel(
            name='TaskLink',
            fields=[
                ('id', models.UUIDField(default=c4rsys.platform.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('object_type', models.CharField(max_length=20)),
                ('object_id', models.UUIDField()),
                ('created_by_user_id', models.UUIDField()),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='companies.company')),
                ('task', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='links', to='tasks.task')),
            ],
            options={
                'db_table': 'task_link',
            },
        ),
        migrations.CreateModel(
            name='TaskPolicy',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('executor_may_release', models.BooleanField(default=True)),
                ('executor_may_change_due', models.BooleanField(default=False)),
                ('updated_by_user_id', models.UUIDField()),
                ('updated_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('version', models.PositiveIntegerField(default=1)),
                ('company', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='companies.company')),
            ],
            options={
                'db_table': 'task_policy',
            },
        ),
        migrations.AddIndex(
            model_name='task',
            index=models.Index(fields=['company', 'state'], name='task_company_state_idx'),
        ),
        migrations.AddIndex(
            model_name='task',
            index=models.Index(fields=['company', 'object_type', 'object_id'], name='task_object_idx'),
        ),
        migrations.AddConstraint(
            model_name='task',
            constraint=models.UniqueConstraint(fields=('company', 'id'), name='task_company_id_uniq'),
        ),
        migrations.AddConstraint(
            model_name='task',
            constraint=models.UniqueConstraint(fields=('company', 'number'), name='task_number_uniq'),
        ),
        migrations.AddConstraint(
            model_name='task',
            constraint=models.CheckConstraint(condition=models.Q(('title', ''), _negated=True), name='task_title_not_empty'),
        ),
        migrations.AddConstraint(
            model_name='task',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('object_id__isnull', True), ('object_type', '')), models.Q(models.Q(('object_type', ''), _negated=True), ('object_id__isnull', False)), _connector='OR'), name='task_object_pair'),
        ),
        migrations.AddConstraint(
            model_name='task',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('closed_at__isnull', False), ('state__in', ['done', 'cancelled'])), models.Q(models.Q(('state__in', ['done', 'cancelled']), _negated=True), ('closed_at__isnull', True)), _connector='OR'), name='task_closed_when_final'),
        ),
        migrations.AddConstraint(
            model_name='task',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('source', 'manual'), ('system_event', '')), models.Q(('source', 'system'), models.Q(('system_event', ''), _negated=True)), _connector='OR'), name='task_system_has_event'),
        ),
        migrations.AddConstraint(
            model_name='taskcomment',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('text', ''), _negated=True), ('file__isnull', False), _connector='OR'), name='task_comment_not_empty'),
        ),
        migrations.AddIndex(
            model_name='tasklink',
            index=models.Index(fields=['company', 'object_type', 'object_id'], name='task_link_idx'),
        ),
        migrations.AddConstraint(
            model_name='tasklink',
            constraint=models.UniqueConstraint(fields=('task', 'object_type', 'object_id'), name='task_link_uniq'),
        ),
        migrations.RunSQL(LINKS, UNLINK),
        migrations.RunPython(add, remove),
    ]
