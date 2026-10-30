"""Inspection as its own record, templates, policies and damages (part О1–О3 of block 8)."""

import c4rsys.platform.ids
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('companies', '0025_order_discount_permission'),
        ('files', '0007_inspection_files'),
        ('fleet', '0017_owner_request_closed_reason'),
        ('inspections', '0002_inspection_links_and_immutability'),
        ('parties', '0009_one_card_per_profile'),
        ('platform', '0004_companycounter'),
        ('rentals', '0020_rental_proposals'),
        ('service', '0006_job_editing'),
    ]

    operations = [
        migrations.CreateModel(
            name='Damage',
            fields=[
                ('id', models.UUIDField(default=c4rsys.platform.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('attributed_to_rental', models.BooleanField(default=False)),
                ('performed_at', models.DateTimeField()),
                ('odometer_value', models.DecimalField(blank=True, decimal_places=1, max_digits=12, null=True)),
                ('odometer_unit', models.CharField(max_length=2)),
                ('zone_scheme', models.CharField(blank=True, default='', max_length=30)),
                ('zone_x', models.DecimalField(blank=True, decimal_places=4, max_digits=5, null=True)),
                ('zone_y', models.DecimalField(blank=True, decimal_places=4, max_digits=5, null=True)),
                ('zone_text', models.CharField(blank=True, default='', max_length=300)),
                ('description', models.TextField(blank=True, default='')),
                ('state', models.CharField(choices=[('actual', 'Actual'), ('fixed', 'Fixed'), ('erroneous', 'Erroneous')], default='actual', max_length=10)),
                ('estimate_amount', models.DecimalField(blank=True, decimal_places=4, max_digits=20, null=True)),
                ('created_by_user_id', models.UUIDField()),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('version', models.PositiveIntegerField(default=1)),
            ],
            options={
                'db_table': 'damage',
            },
        ),
        migrations.CreateModel(
            name='DamageEvent',
            fields=[
                ('id', models.UUIDField(default=c4rsys.platform.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('kind', models.CharField(choices=[('created', 'Created'), ('estimated', 'Estimated'), ('fixed', 'Fixed'), ('returned', 'Returned'), ('erroneous', 'Erroneous')], max_length=12)),
                ('state_before', models.CharField(blank=True, default='', max_length=10)),
                ('state_after', models.CharField(max_length=10)),
                ('occurred_at', models.DateTimeField()),
                ('odometer_value', models.DecimalField(blank=True, decimal_places=1, max_digits=12, null=True)),
                ('basis_type', models.CharField(blank=True, default='', max_length=20)),
                ('basis_id', models.UUIDField(blank=True, null=True)),
                ('estimate_amount', models.DecimalField(blank=True, decimal_places=4, max_digits=20, null=True)),
                ('estimate_currency', models.CharField(blank=True, default='', max_length=3)),
                ('comment', models.TextField(blank=True, default='')),
                ('created_by_user_id', models.UUIDField()),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={
                'db_table': 'damage_event',
            },
        ),
        migrations.CreateModel(
            name='DamageFile',
            fields=[
                ('id', models.UUIDField(default=c4rsys.platform.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('role', models.CharField(choices=[('selected', 'Selected'), ('fix_result', 'Fix Result')], max_length=12)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={
                'db_table': 'damage_file',
            },
        ),
        migrations.CreateModel(
            name='InspectionFile',
            fields=[
                ('id', models.UUIDField(default=c4rsys.platform.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('kind', models.CharField(choices=[('photo', 'Photo'), ('video', 'Video')], max_length=10)),
                ('position', models.CharField(blank=True, default='', max_length=60)),
                ('added_by_user_id', models.UUIDField()),
                ('added_at', models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={
                'db_table': 'inspection_file',
            },
        ),
        migrations.CreateModel(
            name='InspectionMissingReason',
            fields=[
                ('id', models.UUIDField(default=c4rsys.platform.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('kind', models.CharField(choices=[('issue', 'Issue'), ('return', 'Return'), ('out_of_rental', 'Out Of Rental'), ('intermediate', 'Intermediate')], max_length=20)),
                ('reason', models.TextField()),
                ('created_by_user_id', models.UUIDField()),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={
                'db_table': 'inspection_missing_reason',
            },
        ),
        migrations.CreateModel(
            name='InspectionPolicy',
            fields=[
                ('id', models.UUIDField(default=c4rsys.platform.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('kind', models.CharField(choices=[('issue', 'Issue'), ('return', 'Return'), ('out_of_rental', 'Out Of Rental'), ('intermediate', 'Intermediate')], max_length=20)),
                ('application', models.CharField(choices=[('rental', 'Rental'), ('service', 'Service'), ('standalone', 'Standalone')], max_length=10)),
                ('check_required', models.BooleanField(blank=True, null=True)),
                ('odometer_required', models.BooleanField(default=False)),
                ('fuel_required', models.BooleanField(default=False)),
                ('cleanliness_required', models.BooleanField(default=False)),
                ('updated_by_user_id', models.UUIDField()),
                ('updated_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('version', models.PositiveIntegerField(default=1)),
            ],
            options={
                'db_table': 'inspection_policy',
            },
        ),
        migrations.CreateModel(
            name='InspectionTemplate',
            fields=[
                ('id', models.UUIDField(default=c4rsys.platform.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('transport', models.CharField(choices=[('car', 'Car'), ('motorcycle', 'Motorcycle')], max_length=12)),
                ('current_number', models.PositiveIntegerField(default=1)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={
                'db_table': 'inspection_template',
            },
        ),
        migrations.CreateModel(
            name='InspectionTemplateVersion',
            fields=[
                ('id', models.UUIDField(default=c4rsys.platform.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('number', models.PositiveIntegerField()),
                ('positions', models.JSONField()),
                ('created_by_user_id', models.UUIDField(blank=True, null=True)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={
                'db_table': 'inspection_template_version',
            },
        ),
        migrations.RemoveConstraint(
            model_name='inspection',
            name='inspection_saved_has_time',
        ),
        migrations.RemoveConstraint(
            model_name='inspection',
            name='inspection_other_place_described',
        ),
        migrations.AddField(
            model_name='inspection',
            name='application',
            field=models.CharField(choices=[('rental', 'Rental'), ('service', 'Service'), ('standalone', 'Standalone')], default='standalone', max_length=10),
        ),
        migrations.AddField(
            model_name='inspection',
            name='check_required',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='inspection',
            name='cleanliness',
            field=models.CharField(blank=True, default='', max_length=200),
        ),
        migrations.AddField(
            model_name='inspection',
            name='damages_draft',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='inspection',
            name='end_reason',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='inspection',
            name='ended_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='inspection',
            name='ended_by_user_id',
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='inspection',
            name='invalidated_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='inspection',
            name='invalidated_by_user_id',
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='inspection',
            name='origin',
            field=models.CharField(choices=[('rental', 'Rental'), ('form', 'Form')], default='form', max_length=10),
        ),
        migrations.AddField(
            model_name='inspection',
            name='participant_client',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='parties.clientcard'),
        ),
        migrations.AddField(
            model_name='inspection',
            name='participant_counterparty',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='parties.counterparty'),
        ),
        migrations.AddField(
            model_name='inspection',
            name='participant_employment',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='companies.employment'),
        ),
        migrations.AddField(
            model_name='inspection',
            name='participant_kind',
            field=models.CharField(blank=True, choices=[('', 'None'), ('client', 'Client'), ('counterparty', 'Counterparty'), ('employee', 'Employee'), ('other', 'Other')], default='', max_length=12),
        ),
        migrations.AddField(
            model_name='inspection',
            name='participant_text',
            field=models.CharField(blank=True, default='', max_length=300),
        ),
        migrations.AddField(
            model_name='inspection',
            name='place_address',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
        migrations.AddField(
            model_name='inspection',
            name='place_lat',
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True),
        ),
        migrations.AddField(
            model_name='inspection',
            name='place_lng',
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True),
        ),
        migrations.AddField(
            model_name='inspection',
            name='planned_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='inspection',
            name='service_operation',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='service.serviceoperation'),
        ),
        migrations.AddField(
            model_name='inspection',
            name='started_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='inspection',
            name='version',
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AlterField(
            model_name='inspection',
            name='place_location',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='companies.location'),
        ),
        migrations.AlterField(
            model_name='inspection',
            name='state',
            field=models.CharField(choices=[('draft', 'Draft'), ('planned', 'Planned'), ('in_progress', 'In Progress'), ('saved', 'Saved'), ('cancelled', 'Cancelled'), ('terminated', 'Terminated'), ('invalid', 'Invalid')], max_length=12),
        ),
        migrations.AddField(
            model_name='damage',
            name='company',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='companies.company'),
        ),
        migrations.AddField(
            model_name='damage',
            name='estimate_currency',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='platform.currency'),
        ),
        migrations.AddField(
            model_name='damage',
            name='inspection',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='inspections.inspection'),
        ),
        migrations.AddField(
            model_name='damage',
            name='rental',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='rentals.rental'),
        ),
        migrations.AddField(
            model_name='damage',
            name='vehicle',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='fleet.vehicle'),
        ),
        migrations.AddField(
            model_name='damage',
            name='vehicle_card',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='fleet.vehiclecard'),
        ),
        migrations.AddField(
            model_name='damageevent',
            name='company',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='companies.company'),
        ),
        migrations.AddField(
            model_name='damageevent',
            name='damage',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='events', to='inspections.damage'),
        ),
        migrations.AddField(
            model_name='damagefile',
            name='company',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='companies.company'),
        ),
        migrations.AddField(
            model_name='damagefile',
            name='damage',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='files', to='inspections.damage'),
        ),
        migrations.AddField(
            model_name='damagefile',
            name='event',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='inspections.damageevent'),
        ),
        migrations.AddField(
            model_name='damagefile',
            name='file',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='files.storedfile'),
        ),
        migrations.AddField(
            model_name='inspectionfile',
            name='company',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='companies.company'),
        ),
        migrations.AddField(
            model_name='inspectionfile',
            name='file',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='files.storedfile'),
        ),
        migrations.AddField(
            model_name='inspectionfile',
            name='inspection',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='files', to='inspections.inspection'),
        ),
        migrations.AddField(
            model_name='inspectionmissingreason',
            name='company',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='companies.company'),
        ),
        migrations.AddField(
            model_name='inspectionmissingreason',
            name='rental',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='rentals.rental'),
        ),
        migrations.AddField(
            model_name='inspectionpolicy',
            name='company',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='companies.company'),
        ),
        migrations.AddField(
            model_name='inspectiontemplate',
            name='company',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='companies.company'),
        ),
        migrations.AddField(
            model_name='inspectiontemplateversion',
            name='company',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='companies.company'),
        ),
        migrations.AddField(
            model_name='inspectiontemplateversion',
            name='template',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='versions', to='inspections.inspectiontemplate'),
        ),
        migrations.AddField(
            model_name='inspection',
            name='template_version',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='inspections.inspectiontemplateversion'),
        ),
        # Existing records are readings of the rental card (§ 10.1.4); the trigger of 0002
        # forbids changing a saved record, so it is off for this one update.
        migrations.RunSQL(
            sql="""
ALTER TABLE inspection DISABLE TRIGGER inspection_immutable;
UPDATE inspection SET application = 'rental', origin = 'rental' WHERE rental_id IS NOT NULL;
UPDATE inspection SET check_required = TRUE WHERE check_status = 'required_not_done';
ALTER TABLE inspection ENABLE TRIGGER inspection_immutable;
""",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.AddIndex(
            model_name='inspection',
            index=models.Index(fields=['company', 'vehicle_card'], name='inspection_vehicle_idx'),
        ),
        migrations.AddIndex(
            model_name='inspection',
            index=models.Index(fields=['rental'], name='inspection_rental_idx'),
        ),
        migrations.AddConstraint(
            model_name='inspection',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('state__in', ['saved', 'invalid']), _negated=True), ('saved_at__isnull', False), _connector='OR'), name='inspection_saved_has_time_v2'),
        ),
        migrations.AddConstraint(
            model_name='inspection',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('place_kind', 'location'), ('place_location__isnull', False)), models.Q(('place_kind', 'other'), models.Q(models.Q(('place_note', ''), _negated=True), models.Q(('place_address', ''), _negated=True), ('place_lat__isnull', False), _connector='OR')), _connector='OR'), name='inspection_place_described'),
        ),
        migrations.AddConstraint(
            model_name='inspection',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('application', 'rental'), ('rental__isnull', False), ('service_operation', None)), models.Q(('application', 'service'), ('rental', None), ('service_operation__isnull', False)), models.Q(('application', 'standalone'), ('rental', None), ('service_operation', None)), _connector='OR'), name='inspection_application_link'),
        ),
        migrations.AddConstraint(
            model_name='inspection',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('kind', 'intermediate'), _negated=True), ('application__in', ['rental', 'service']), _connector='OR'), name='inspection_intermediate_in_operation'),
        ),
        migrations.AddConstraint(
            model_name='inspection',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('kind', 'out_of_rental'), _negated=True), models.Q(('application', 'rental'), _negated=True), _connector='OR'), name='inspection_out_of_rental_not_in_rental'),
        ),
        migrations.AddConstraint(
            model_name='inspection',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('state__in', ['cancelled', 'terminated']), _negated=True), models.Q(('end_reason', ''), _negated=True), _connector='OR'), name='inspection_end_has_reason'),
        ),
        migrations.AddConstraint(
            model_name='inspection',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('state', 'invalid'), _negated=True), models.Q(('invalidated_reason', ''), _negated=True), _connector='OR'), name='inspection_invalid_has_reason'),
        ),
        migrations.AddConstraint(
            model_name='inspection',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('participant_kind', ''), ('participant_text', '')), models.Q(('participant_client__isnull', False), ('participant_kind', 'client')), models.Q(('participant_counterparty__isnull', False), ('participant_kind', 'counterparty')), models.Q(('participant_employment__isnull', False), ('participant_kind', 'employee')), models.Q(('participant_kind', 'other'), models.Q(('participant_text', ''), _negated=True)), _connector='OR'), name='inspection_participant_given'),
        ),
        migrations.AddIndex(
            model_name='damage',
            index=models.Index(fields=['vehicle'], name='damage_vehicle_idx'),
        ),
        migrations.AddConstraint(
            model_name='damage',
            constraint=models.UniqueConstraint(fields=('company', 'id'), name='damage_company_id_uniq'),
        ),
        migrations.AddConstraint(
            model_name='damage',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('estimate_amount__isnull', True), ('estimate_currency__isnull', True)), models.Q(('estimate_amount__gte', 0), ('estimate_currency__isnull', False)), _connector='OR'), name='damage_estimate_valid'),
        ),
        migrations.AddConstraint(
            model_name='damage',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('zone_scheme', ''), ('zone_x__isnull', True), ('zone_y__isnull', True)), models.Q(models.Q(('zone_scheme', ''), _negated=True), ('zone_x__gte', 0), ('zone_x__lte', 1), ('zone_y__gte', 0), ('zone_y__lte', 1)), _connector='OR'), name='damage_zone_point'),
        ),
        migrations.AddConstraint(
            model_name='damage',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('description', ''), _negated=True), models.Q(('zone_text', ''), _negated=True), models.Q(('zone_scheme', ''), _negated=True), _connector='OR'), name='damage_described'),
        ),
        migrations.AddConstraint(
            model_name='damage',
            constraint=models.CheckConstraint(condition=models.Q(('attributed_to_rental', False), ('rental__isnull', False), _connector='OR'), name='damage_attributed_has_rental'),
        ),
        migrations.AddConstraint(
            model_name='inspectionfile',
            constraint=models.UniqueConstraint(fields=('company', 'id'), name='inspection_file_company_id_uniq'),
        ),
        migrations.AddConstraint(
            model_name='inspectionfile',
            constraint=models.UniqueConstraint(fields=('inspection', 'file'), name='inspection_file_once'),
        ),
        migrations.AddConstraint(
            model_name='inspectionfile',
            constraint=models.CheckConstraint(condition=models.Q(('kind', 'photo'), ('position', ''), _connector='OR'), name='inspection_file_video_extra'),
        ),
        migrations.AddConstraint(
            model_name='inspectionmissingreason',
            constraint=models.CheckConstraint(condition=models.Q(('reason', ''), _negated=True), name='inspection_missing_reason_text'),
        ),
        migrations.AddConstraint(
            model_name='inspectionmissingreason',
            constraint=models.CheckConstraint(condition=models.Q(('kind__in', ['issue', 'return'])), name='inspection_missing_reason_kind'),
        ),
        migrations.AddConstraint(
            model_name='inspectionpolicy',
            constraint=models.UniqueConstraint(fields=('company', 'kind', 'application'), name='inspection_policy_uniq'),
        ),
        migrations.AddConstraint(
            model_name='inspectionpolicy',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('application', 'rental'), ('kind__in', ['issue', 'return']), _negated=True), models.Q(('check_required__isnull', True), ('fuel_required', True), ('odometer_required', True)), _connector='OR'), name='inspection_policy_rental_readings'),
        ),
        migrations.AddConstraint(
            model_name='inspectionpolicy',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('application', 'rental'), ('kind__in', ['issue', 'return'])), ('check_required__isnull', False), _connector='OR'), name='inspection_policy_check_given'),
        ),
        migrations.AddConstraint(
            model_name='inspectiontemplate',
            constraint=models.UniqueConstraint(fields=('company', 'id'), name='inspection_template_company_id_uniq'),
        ),
        migrations.AddConstraint(
            model_name='inspectiontemplate',
            constraint=models.UniqueConstraint(fields=('company', 'transport'), name='inspection_template_one_per_transport'),
        ),
        migrations.AddConstraint(
            model_name='inspectiontemplateversion',
            constraint=models.UniqueConstraint(fields=('template', 'number'), name='inspection_template_ver'),
        ),
    ]
