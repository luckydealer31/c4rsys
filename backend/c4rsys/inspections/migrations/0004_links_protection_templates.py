"""Links within one company (A05), protection of saved records (§ 10.1.6, § 10.5.1) and the
ready templates of the companies that already exist (§ 10.3.5)."""

from typing import Any

from django.db import migrations

FORWARD = """
ALTER TABLE inspection_template_version
    ADD CONSTRAINT inspection_template_version_company_id_uniq UNIQUE (company_id, id),
    ADD CONSTRAINT inspection_template_version_template_same_company_fk
    FOREIGN KEY (company_id, template_id) REFERENCES inspection_template (company_id, id);

ALTER TABLE inspection
    ADD CONSTRAINT inspection_service_operation_same_company_fk
    FOREIGN KEY (company_id, service_operation_id) REFERENCES service_operation (company_id, id),
    ADD CONSTRAINT inspection_participant_client_same_company_fk
    FOREIGN KEY (company_id, participant_client_id) REFERENCES client_card (company_id, id),
    ADD CONSTRAINT inspection_participant_counterparty_same_company_fk
    FOREIGN KEY (company_id, participant_counterparty_id) REFERENCES counterparty (company_id, id),
    ADD CONSTRAINT inspection_participant_employment_same_company_fk
    FOREIGN KEY (company_id, participant_employment_id) REFERENCES employment (company_id, id),
    ADD CONSTRAINT inspection_template_version_same_company_fk
    FOREIGN KEY (company_id, template_version_id)
    REFERENCES inspection_template_version (company_id, id);

ALTER TABLE inspection_file
    ADD CONSTRAINT inspection_file_inspection_same_company_fk
    FOREIGN KEY (company_id, inspection_id) REFERENCES inspection (company_id, id);

ALTER TABLE inspection_missing_reason
    ADD CONSTRAINT inspection_missing_reason_rental_same_company_fk
    FOREIGN KEY (company_id, rental_id) REFERENCES rental (company_id, id);

ALTER TABLE damage
    ADD CONSTRAINT damage_vehicle_card_same_company_fk
    FOREIGN KEY (company_id, vehicle_card_id) REFERENCES vehicle_card (company_id, id),
    ADD CONSTRAINT damage_inspection_same_company_fk
    FOREIGN KEY (company_id, inspection_id) REFERENCES inspection (company_id, id),
    ADD CONSTRAINT damage_rental_same_company_fk
    FOREIGN KEY (company_id, rental_id) REFERENCES rental (company_id, id);

ALTER TABLE damage_event
    ADD CONSTRAINT damage_event_damage_same_company_fk
    FOREIGN KEY (company_id, damage_id) REFERENCES damage (company_id, id);

ALTER TABLE damage_file
    ADD CONSTRAINT damage_file_damage_same_company_fk
    FOREIGN KEY (company_id, damage_id) REFERENCES damage (company_id, id);

-- § 10.1.6, § 10.6.3: a saved inspection may only be marked invalid with a reason; a cancelled,
-- terminated or invalid one does not change at all; only a draft is deleted.
CREATE OR REPLACE FUNCTION inspection_forbid_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.state <> 'draft' THEN
            RAISE EXCEPTION 'only a draft inspection can be deleted'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.state IN ('invalid', 'cancelled', 'terminated') THEN
        RAISE EXCEPTION 'a finished inspection cannot be changed'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.state = 'saved' THEN
        IF NEW.state <> 'invalid' OR NEW.invalidated_reason = ''
           OR (to_jsonb(NEW) - 'state' - 'invalidated_reason' - 'invalidated_at'
               - 'invalidated_by_user_id' - 'version')
              IS DISTINCT FROM (to_jsonb(OLD) - 'state' - 'invalidated_reason'
               - 'invalidated_at' - 'invalidated_by_user_id' - 'version') THEN
            RAISE EXCEPTION 'saved inspection is immutable'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

-- Photos and videos are added and removed only while the inspection is filled in.
CREATE FUNCTION inspection_file_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    parent_state text;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'inspection_file is not changed'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    SELECT state INTO parent_state FROM inspection
        WHERE id = CASE WHEN TG_OP = 'DELETE' THEN OLD.inspection_id ELSE NEW.inspection_id END;
    IF parent_state IS DISTINCT FROM 'draft' AND parent_state IS DISTINCT FROM 'in_progress' THEN
        RAISE EXCEPTION 'files of an inspection change only while it is filled in'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER inspection_file_protect
BEFORE INSERT OR UPDATE OR DELETE ON inspection_file
FOR EACH ROW EXECUTE FUNCTION inspection_file_guard();

CREATE FUNCTION inspections_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME
        USING ERRCODE = 'insufficient_privilege';
END;
$$;

CREATE TRIGGER inspection_template_version_append_only
BEFORE UPDATE OR DELETE ON inspection_template_version
FOR EACH ROW EXECUTE FUNCTION inspections_append_only();

CREATE TRIGGER inspection_missing_reason_append_only
BEFORE UPDATE OR DELETE ON inspection_missing_reason
FOR EACH ROW EXECUTE FUNCTION inspections_append_only();

CREATE TRIGGER damage_event_append_only
BEFORE UPDATE OR DELETE ON damage_event
FOR EACH ROW EXECUTE FUNCTION inspections_append_only();

CREATE TRIGGER damage_file_append_only
BEFORE UPDATE OR DELETE ON damage_file
FOR EACH ROW EXECUTE FUNCTION inspections_append_only();

-- § 10.5.2: the facts of a damage never change; its state and estimate change with an event.
CREATE FUNCTION damage_forbid_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'damage is never deleted'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF (to_jsonb(NEW) - 'state' - 'estimate_amount' - 'estimate_currency_id' - 'version')
       IS DISTINCT FROM
       (to_jsonb(OLD) - 'state' - 'estimate_amount' - 'estimate_currency_id' - 'version') THEN
        RAISE EXCEPTION 'damage: only its state and estimate change'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER damage_protect
BEFORE UPDATE OR DELETE ON damage
FOR EACH ROW EXECUTE FUNCTION damage_forbid_change();
"""

REVERSE = """
DROP TRIGGER IF EXISTS damage_protect ON damage;
DROP FUNCTION IF EXISTS damage_forbid_change();
DROP TRIGGER IF EXISTS damage_file_append_only ON damage_file;
DROP TRIGGER IF EXISTS damage_event_append_only ON damage_event;
DROP TRIGGER IF EXISTS inspection_missing_reason_append_only ON inspection_missing_reason;
DROP TRIGGER IF EXISTS inspection_template_version_append_only ON inspection_template_version;
DROP FUNCTION IF EXISTS inspections_append_only();
DROP TRIGGER IF EXISTS inspection_file_protect ON inspection_file;
DROP FUNCTION IF EXISTS inspection_file_guard();
ALTER TABLE damage_file DROP CONSTRAINT IF EXISTS damage_file_damage_same_company_fk;
ALTER TABLE damage_event DROP CONSTRAINT IF EXISTS damage_event_damage_same_company_fk;
ALTER TABLE damage
    DROP CONSTRAINT IF EXISTS damage_rental_same_company_fk,
    DROP CONSTRAINT IF EXISTS damage_inspection_same_company_fk,
    DROP CONSTRAINT IF EXISTS damage_vehicle_card_same_company_fk;
ALTER TABLE inspection_missing_reason
    DROP CONSTRAINT IF EXISTS inspection_missing_reason_rental_same_company_fk;
ALTER TABLE inspection_file DROP CONSTRAINT IF EXISTS inspection_file_inspection_same_company_fk;
ALTER TABLE inspection
    DROP CONSTRAINT IF EXISTS inspection_template_version_same_company_fk,
    DROP CONSTRAINT IF EXISTS inspection_participant_employment_same_company_fk,
    DROP CONSTRAINT IF EXISTS inspection_participant_counterparty_same_company_fk,
    DROP CONSTRAINT IF EXISTS inspection_participant_client_same_company_fk,
    DROP CONSTRAINT IF EXISTS inspection_service_operation_same_company_fk;
ALTER TABLE inspection_template_version
    DROP CONSTRAINT IF EXISTS inspection_template_version_template_same_company_fk,
    DROP CONSTRAINT IF EXISTS inspection_template_version_company_id_uniq;
"""


def seed_templates(apps: Any, schema_editor: Any) -> None:
    from c4rsys.inspections.templates import default_positions

    template = apps.get_model("inspections", "InspectionTemplate")
    version = apps.get_model("inspections", "InspectionTemplateVersion")
    for company_id in apps.get_model("companies", "Company").objects.values_list("id", flat=True):
        for transport in ("car", "motorcycle"):
            item, created = template.objects.get_or_create(
                company_id=company_id, transport=transport
            )
            if created:
                version.objects.create(
                    company_id=company_id,
                    template=item,
                    number=1,
                    positions=default_positions(transport),
                )


class Migration(migrations.Migration):
    dependencies = [
        ("inspections", "0003_records_templates_damages"),
        ("companies", "0025_order_discount_permission"),
        ("parties", "0009_one_card_per_profile"),
        ("service", "0006_job_editing"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE),
        migrations.RunPython(seed_templates, migrations.RunPython.noop),
    ]
