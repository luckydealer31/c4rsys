"""Extensions of tasks (§ 19.4): checklists, groups, tags, dependencies and repeating series;
rows bound to the same company."""

import c4rsys.platform.ids
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


LINKS = """
ALTER TABLE task
    ADD CONSTRAINT task_group_same_company_fk
    FOREIGN KEY (company_id, group_id) REFERENCES task_group (company_id, id),
    ADD CONSTRAINT task_series_same_company_fk
    FOREIGN KEY (company_id, series_id) REFERENCES task_series (company_id, id);
ALTER TABLE task_checklist_item
    ADD CONSTRAINT task_checklist_task_same_company_fk
    FOREIGN KEY (company_id, task_id) REFERENCES task (company_id, id);
ALTER TABLE task_tag_link
    ADD CONSTRAINT task_tag_link_task_same_company_fk
    FOREIGN KEY (company_id, task_id) REFERENCES task (company_id, id),
    ADD CONSTRAINT task_tag_link_tag_same_company_fk
    FOREIGN KEY (company_id, tag_id) REFERENCES task_tag (company_id, id);
ALTER TABLE task_dependency
    ADD CONSTRAINT task_dependency_task_same_company_fk
    FOREIGN KEY (company_id, task_id) REFERENCES task (company_id, id),
    ADD CONSTRAINT task_dependency_predecessor_same_company_fk
    FOREIGN KEY (company_id, predecessor_id) REFERENCES task (company_id, id);
ALTER TABLE task_series
    ADD CONSTRAINT task_series_location_same_company_fk
    FOREIGN KEY (company_id, location_id) REFERENCES location (company_id, id),
    ADD CONSTRAINT task_series_assignee_same_company_fk
    FOREIGN KEY (company_id, assignee_id) REFERENCES employment (company_id, id),
    ADD CONSTRAINT task_series_group_same_company_fk
    FOREIGN KEY (company_id, group_id) REFERENCES task_group (company_id, id);
"""
UNLINK = """
ALTER TABLE task_series
    DROP CONSTRAINT task_series_location_same_company_fk,
    DROP CONSTRAINT task_series_assignee_same_company_fk,
    DROP CONSTRAINT task_series_group_same_company_fk;
ALTER TABLE task_dependency
    DROP CONSTRAINT task_dependency_task_same_company_fk,
    DROP CONSTRAINT task_dependency_predecessor_same_company_fk;
ALTER TABLE task_tag_link
    DROP CONSTRAINT task_tag_link_task_same_company_fk,
    DROP CONSTRAINT task_tag_link_tag_same_company_fk;
ALTER TABLE task_checklist_item DROP CONSTRAINT task_checklist_task_same_company_fk;
ALTER TABLE task
    DROP CONSTRAINT task_group_same_company_fk,
    DROP CONSTRAINT task_series_same_company_fk;
"""


class Migration(migrations.Migration):

    dependencies = [
        ('companies', '0020_order_reopen_permission'),
        ('tasks', '0001_tasks'),
    ]

    operations = [
        migrations.CreateModel(
            name='TaskTag',
            fields=[
                ('id', models.UUIDField(default=c4rsys.platform.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=100)),
                ('created_by_user_id', models.UUIDField()),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={
                'db_table': 'task_tag',
            },
        ),
        migrations.CreateModel(
            name='TaskTagLink',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ],
            options={
                'db_table': 'task_tag_link',
            },
        ),
        migrations.AddField(
            model_name='task',
            name='checklist_required',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='task',
            name='series_on',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name='TaskChecklistItem',
            fields=[
                ('id', models.UUIDField(default=c4rsys.platform.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('position', models.PositiveIntegerField()),
                ('text', models.CharField(max_length=500)),
                ('done', models.BooleanField(default=False)),
                ('done_at', models.DateTimeField(blank=True, null=True)),
                ('done_by_user_id', models.UUIDField(blank=True, null=True)),
                ('updated_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('updated_by_user_id', models.UUIDField()),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='companies.company')),
                ('task', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='checklist', to='tasks.task')),
            ],
            options={
                'db_table': 'task_checklist_item',
            },
        ),
        migrations.CreateModel(
            name='TaskDependency',
            fields=[
                ('id', models.UUIDField(default=c4rsys.platform.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('created_by_user_id', models.UUIDField()),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='companies.company')),
                ('predecessor', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='successor_links', to='tasks.task')),
                ('task', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='predecessor_links', to='tasks.task')),
            ],
            options={
                'db_table': 'task_dependency',
            },
        ),
        migrations.CreateModel(
            name='TaskGroup',
            fields=[
                ('id', models.UUIDField(default=c4rsys.platform.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=200)),
                ('description', models.TextField(blank=True, default='')),
                ('created_by_user_id', models.UUIDField()),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('version', models.PositiveIntegerField(default=1)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='companies.company')),
            ],
            options={
                'db_table': 'task_group',
            },
        ),
        migrations.AddField(
            model_name='task',
            name='group',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tasks.taskgroup'),
        ),
        migrations.CreateModel(
            name='TaskSeries',
            fields=[
                ('id', models.UUIDField(default=c4rsys.platform.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('frequency', models.CharField(choices=[('daily', 'Daily'), ('weekly', 'Weekly'), ('monthly', 'Monthly')], max_length=10)),
                ('weekdays', models.JSONField(blank=True, default=list)),
                ('day_of_month', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('starts_on', models.DateField()),
                ('ends_on', models.DateField(blank=True, null=True)),
                ('next_on', models.DateField(blank=True, null=True)),
                ('state', models.CharField(choices=[('active', 'Active'), ('paused', 'Paused'), ('stopped', 'Stopped')], default='active', max_length=10)),
                ('title', models.CharField(max_length=300)),
                ('description', models.TextField(blank=True, default='')),
                ('priority', models.CharField(default='normal', max_length=10)),
                ('object_type', models.CharField(blank=True, default='', max_length=20)),
                ('object_id', models.UUIDField(blank=True, null=True)),
                ('due_time', models.TimeField(blank=True, null=True)),
                ('checklist', models.JSONField(blank=True, default=list)),
                ('checklist_required', models.BooleanField(default=False)),
                ('tag_ids', models.JSONField(blank=True, default=list)),
                ('created_by_user_id', models.UUIDField()),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('version', models.PositiveIntegerField(default=1)),
                ('assignee', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='companies.employment')),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='companies.company')),
                ('created_by_employment', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='companies.employment')),
                ('group', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tasks.taskgroup')),
                ('location', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='companies.location')),
            ],
            options={
                'db_table': 'task_series',
            },
        ),
        migrations.AddField(
            model_name='task',
            name='series',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='tasks', to='tasks.taskseries'),
        ),
        migrations.AddConstraint(
            model_name='task',
            constraint=models.UniqueConstraint(condition=models.Q(('series__isnull', False)), fields=('series', 'series_on'), name='task_series_one_per_date'),
        ),
        migrations.AddConstraint(
            model_name='task',
            constraint=models.CheckConstraint(condition=models.Q(('series__isnull', True), ('series_on__isnull', False), _connector='OR'), name='task_series_has_date'),
        ),
        migrations.AddField(
            model_name='tasktag',
            name='company',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='companies.company'),
        ),
        migrations.AddField(
            model_name='tasktaglink',
            name='company',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='companies.company'),
        ),
        migrations.AddField(
            model_name='tasktaglink',
            name='tag',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='links', to='tasks.tasktag'),
        ),
        migrations.AddField(
            model_name='tasktaglink',
            name='task',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='tag_links', to='tasks.task'),
        ),
        migrations.AddConstraint(
            model_name='taskchecklistitem',
            constraint=models.CheckConstraint(condition=models.Q(('text', ''), _negated=True), name='task_checklist_text_not_empty'),
        ),
        migrations.AddConstraint(
            model_name='taskchecklistitem',
            constraint=models.CheckConstraint(condition=models.Q(('done', False), models.Q(('done_at__isnull', False), ('done_by_user_id__isnull', False)), _connector='OR'), name='task_checklist_done_has_author'),
        ),
        migrations.AddConstraint(
            model_name='taskdependency',
            constraint=models.UniqueConstraint(fields=('task', 'predecessor'), name='task_dependency_uniq'),
        ),
        migrations.AddConstraint(
            model_name='taskdependency',
            constraint=models.CheckConstraint(condition=models.Q(('task', models.F('predecessor')), _negated=True), name='task_dependency_not_self'),
        ),
        migrations.AddConstraint(
            model_name='taskgroup',
            constraint=models.UniqueConstraint(fields=('company', 'id'), name='task_group_company_id_uniq'),
        ),
        migrations.AddConstraint(
            model_name='taskgroup',
            constraint=models.CheckConstraint(condition=models.Q(('name', ''), _negated=True), name='task_group_name_not_empty'),
        ),
        migrations.AddConstraint(
            model_name='taskseries',
            constraint=models.UniqueConstraint(fields=('company', 'id'), name='task_series_company_id_uniq'),
        ),
        migrations.AddConstraint(
            model_name='taskseries',
            constraint=models.CheckConstraint(condition=models.Q(('ends_on__isnull', True), ('ends_on__gte', models.F('starts_on')), _connector='OR'), name='task_series_ends_after_start'),
        ),
        migrations.AddConstraint(
            model_name='tasktag',
            constraint=models.UniqueConstraint(fields=('company', 'id'), name='task_tag_company_id_uniq'),
        ),
        migrations.AddConstraint(
            model_name='tasktag',
            constraint=models.UniqueConstraint(fields=('company', 'name'), name='task_tag_name_uniq'),
        ),
        migrations.AddConstraint(
            model_name='tasktag',
            constraint=models.CheckConstraint(condition=models.Q(('name', ''), _negated=True), name='task_tag_name_not_empty'),
        ),
        migrations.AddConstraint(
            model_name='tasktaglink',
            constraint=models.UniqueConstraint(fields=('task', 'tag'), name='task_tag_link_uniq'),
        ),
        migrations.RunSQL(LINKS, UNLINK),
    ]
