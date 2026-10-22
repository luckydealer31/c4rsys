import django.core.serializers.json
import django.db.models.expressions
import django.utils.timezone
from django.db import migrations, models

import c4rsys.platform.ids


class Migration(migrations.Migration):
    initial = True

    dependencies: list[tuple[str, str]] = []

    operations = [
        migrations.CreateModel(
            name="AuditEvent",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=c4rsys.platform.ids.uuid7,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("occurred_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("company_id", models.UUIDField(blank=True, null=True)),
                ("actor_user_id", models.UUIDField(blank=True, null=True)),
                ("actor_employment_id", models.UUIDField(blank=True, null=True)),
                ("system_process", models.CharField(blank=True, default="", max_length=64)),
                ("session_id", models.UUIDField(blank=True, null=True)),
                ("request_id", models.CharField(blank=True, default="", max_length=64)),
                ("action", models.CharField(max_length=100)),
                ("object_type", models.CharField(max_length=60)),
                ("object_id", models.CharField(max_length=64)),
                (
                    "before",
                    models.JSONField(
                        blank=True,
                        encoder=django.core.serializers.json.DjangoJSONEncoder,
                        null=True,
                    ),
                ),
                (
                    "after",
                    models.JSONField(
                        blank=True,
                        encoder=django.core.serializers.json.DjangoJSONEncoder,
                        null=True,
                    ),
                ),
                ("reason", models.TextField(blank=True, default="")),
                ("result", models.CharField(default="success", max_length=20)),
                ("idempotency_key", models.CharField(blank=True, default="", max_length=200)),
            ],
            options={
                "db_table": "audit_event",
                "indexes": [
                    models.Index(
                        fields=["company_id", "occurred_at"], name="audit_event_company_time_idx"
                    ),
                    models.Index(fields=["object_type", "object_id"], name="audit_event_object_idx"),
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("actor_user_id__isnull", False))
                        | django.db.models.expressions.Q(("system_process", ""), _negated=True),
                        name="audit_event_has_actor",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="IdempotencyRecord",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=c4rsys.platform.ids.uuid7,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("user_id", models.UUIDField()),
                ("company_id", models.UUIDField(blank=True, null=True)),
                ("key", models.CharField(max_length=200)),
                ("command", models.CharField(max_length=100)),
                ("request_hash", models.CharField(max_length=64)),
                ("response_status", models.PositiveSmallIntegerField(default=0)),
                (
                    "response_body",
                    models.JSONField(
                        default=dict, encoder=django.core.serializers.json.DjangoJSONEncoder
                    ),
                ),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={
                "db_table": "idempotency_record",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("user_id", "company_id", "key"),
                        name="idempotency_record_scope_key_uniq",
                        nulls_distinct=False,
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="PublicIdRegistry",
            fields=[
                (
                    "public_id",
                    models.CharField(max_length=40, primary_key=True, serialize=False),
                ),
                ("entity_type", models.CharField(max_length=40)),
                ("entity_id", models.UUIDField()),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={
                "db_table": "public_id_registry",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("entity_type", "entity_id"), name="public_id_registry_entity_uniq"
                    ),
                ],
            },
        ),
    ]
