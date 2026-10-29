"""The notification centre (ТЗ гл. 20): notifications, recipients and repetitions; rows bound
to the same company."""

import c4rsys.platform.ids
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


LINKS = """
ALTER TABLE notification
    ADD CONSTRAINT notification_location_same_company_fk
    FOREIGN KEY (company_id, location_id) REFERENCES location (company_id, id);
ALTER TABLE notification_recipient
    ADD CONSTRAINT notification_recipient_notification_same_company_fk
    FOREIGN KEY (company_id, notification_id) REFERENCES notification (company_id, id),
    ADD CONSTRAINT notification_recipient_employment_same_company_fk
    FOREIGN KEY (company_id, employment_id) REFERENCES employment (company_id, id);
ALTER TABLE notification_repeat
    ADD CONSTRAINT notification_repeat_notification_same_company_fk
    FOREIGN KEY (company_id, notification_id) REFERENCES notification (company_id, id);
"""
UNLINK = """
ALTER TABLE notification_repeat DROP CONSTRAINT notification_repeat_notification_same_company_fk;
ALTER TABLE notification_recipient
    DROP CONSTRAINT notification_recipient_notification_same_company_fk,
    DROP CONSTRAINT notification_recipient_employment_same_company_fk;
ALTER TABLE notification DROP CONSTRAINT notification_location_same_company_fk;
"""


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('companies', '0020_order_reopen_permission'),
    ]

    operations = [
        migrations.CreateModel(
            name='Notification',
            fields=[
                ('id', models.UUIDField(default=c4rsys.platform.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('kind', models.CharField(choices=[('action', 'Action'), ('warning', 'Warning'), ('reminder', 'Reminder'), ('info', 'Info')], max_length=10)),
                ('event', models.CharField(max_length=60)),
                ('condition', models.CharField(max_length=200)),
                ('params', models.JSONField(blank=True, default=dict)),
                ('object_type', models.CharField(blank=True, default='', max_length=20)),
                ('object_id', models.UUIDField(blank=True, null=True)),
                ('state', models.CharField(choices=[('active', 'Active'), ('done', 'Done')], default='active', max_length=10)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('last_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('repeats', models.PositiveIntegerField(default=0)),
                ('done_at', models.DateTimeField(blank=True, null=True)),
                ('done_reason', models.CharField(blank=True, default='', max_length=60)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='companies.company')),
                ('location', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='companies.location')),
            ],
            options={
                'db_table': 'notification',
            },
        ),
        migrations.CreateModel(
            name='NotificationRecipient',
            fields=[
                ('id', models.UUIDField(default=c4rsys.platform.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('read_at', models.DateTimeField(blank=True, null=True)),
                ('added_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='companies.company')),
                ('employment', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='companies.employment')),
                ('notification', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='recipients', to='notifications.notification')),
            ],
            options={
                'db_table': 'notification_recipient',
            },
        ),
        migrations.CreateModel(
            name='NotificationRepeat',
            fields=[
                ('id', models.UUIDField(default=c4rsys.platform.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('at', models.DateTimeField(default=django.utils.timezone.now)),
                ('params', models.JSONField(blank=True, default=dict)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='companies.company')),
                ('notification', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='repetitions', to='notifications.notification')),
            ],
            options={
                'db_table': 'notification_repeat',
            },
        ),
        migrations.AddIndex(
            model_name='notification',
            index=models.Index(fields=['company', 'state'], name='notification_company_state_idx'),
        ),
        migrations.AddConstraint(
            model_name='notification',
            constraint=models.UniqueConstraint(fields=('company', 'id'), name='notification_company_id_uniq'),
        ),
        migrations.AddConstraint(
            model_name='notification',
            constraint=models.UniqueConstraint(condition=models.Q(('state', 'active')), fields=('company', 'condition'), name='notification_one_active_per_condition'),
        ),
        migrations.AddConstraint(
            model_name='notification',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('done_at__isnull', True), ('state', 'active')), models.Q(('done_at__isnull', False), ('state', 'done')), _connector='OR'), name='notification_done_has_time'),
        ),
        migrations.AddIndex(
            model_name='notificationrecipient',
            index=models.Index(fields=['employment', 'read_at'], name='notification_recipient_idx'),
        ),
        migrations.AddConstraint(
            model_name='notificationrecipient',
            constraint=models.UniqueConstraint(fields=('notification', 'employment'), name='notification_recipient_uniq'),
        ),
        migrations.RunSQL(LINKS, UNLINK),
    ]
