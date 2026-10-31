"""Block 12, decisions of 31.10.2026: the owner's newer data of a shared vehicle (В-12.2) and the
dispute of ownership or management after one vehicle was found in two companies' fleets (В-12.3)."""

import c4rsys.platform.ids
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('companies', '0034_vehicle_lifecycle_permissions'),
        ('fleet', '0021_vehicle_lifecycle_links'),
    ]

    operations = [
        migrations.AddField(
            model_name='vehiclecardworkingvalue',
            name='ended_reason',
            field=models.CharField(blank=True, default='', max_length=12),
        ),
        migrations.AddField(
            model_name='vehiclecardworkingvalue',
            name='owner_newer_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='vehiclecardworkingvalue',
            name='origin',
            field=models.CharField(choices=[('proposal', 'Proposal'), ('link', 'Link'), ('card', 'Card'), ('previous', 'Previous')], max_length=10),
        ),
        migrations.CreateModel(
            name='VehicleOperationalDispute',
            fields=[
                ('id', models.UUIDField(default=c4rsys.platform.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('is_open', models.BooleanField(default=True)),
                ('opened_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('closed_at', models.DateTimeField(blank=True, null=True)),
                ('reason', models.TextField(blank=True, default='')),
                ('evidence', models.TextField(blank=True, default='')),
                ('ticket_id', models.UUIDField(blank=True, null=True)),
                ('decided_by_user_id', models.UUIDField(blank=True, null=True)),
                ('conflict', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='operational_disputes', to='fleet.identifierconflict')),
                ('conflict_decision', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='operational_dispute', to='fleet.identifierconflictdecision')),
                ('vehicle', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='fleet.vehicle')),
            ],
            options={
                'db_table': 'vehicle_operational_dispute',
            },
        ),
        migrations.CreateModel(
            name='VehicleOperationalDisputeParty',
            fields=[
                ('id', models.UUIDField(default=c4rsys.platform.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('relation', models.CharField(max_length=10)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='companies.company')),
                ('dispute', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='parties', to='fleet.vehicleoperationaldispute')),
                ('vehicle_card', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='fleet.vehiclecard')),
            ],
            options={
                'db_table': 'vehicle_operational_dispute_party',
            },
        ),
        migrations.AddConstraint(
            model_name='vehicleoperationaldispute',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('closed_at__isnull', True), ('is_open', True)), models.Q(('closed_at__isnull', False), ('is_open', False)), _connector='OR'), name='vehicle_operational_dispute_closed_at'),
        ),
        migrations.AddConstraint(
            model_name='vehicleoperationaldisputeparty',
            constraint=models.UniqueConstraint(fields=('dispute', 'vehicle_card'), name='vehicle_operational_dispute_party_once'),
        ),
        migrations.RunSQL(
            """ALTER TABLE vehicle_operational_dispute_party
    ADD CONSTRAINT vehicle_operational_dispute_party_card_same_company_fk
    FOREIGN KEY (company_id, vehicle_card_id) REFERENCES vehicle_card (company_id, id);""",
            "ALTER TABLE vehicle_operational_dispute_party "
            "DROP CONSTRAINT vehicle_operational_dispute_party_card_same_company_fk;",
        ),
    ]
