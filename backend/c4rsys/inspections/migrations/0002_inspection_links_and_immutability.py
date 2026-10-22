"""Links within one company (A05) and immutability of a saved inspection (§ 10.1.6)."""

from django.db import migrations

FORWARD = """
ALTER TABLE inspection
    ADD CONSTRAINT inspection_vehicle_card_same_company_fk
    FOREIGN KEY (company_id, vehicle_card_id) REFERENCES vehicle_card (company_id, id),
    ADD CONSTRAINT inspection_rental_same_company_fk
    FOREIGN KEY (company_id, rental_id) REFERENCES rental (company_id, id),
    ADD CONSTRAINT inspection_place_location_same_company_fk
    FOREIGN KEY (company_id, place_location_id) REFERENCES location (company_id, id);

-- A saved inspection may only be marked invalid with a reason; nothing else changes and it is
-- never deleted. An invalid one does not change at all.
CREATE FUNCTION inspection_forbid_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.state <> 'draft' THEN
            RAISE EXCEPTION 'saved inspection cannot be deleted'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.state = 'invalid' THEN
        RAISE EXCEPTION 'invalid inspection cannot be changed'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.state = 'saved' THEN
        IF NEW.state <> 'invalid' OR NEW.invalidated_reason = ''
           OR (to_jsonb(NEW) - 'state' - 'invalidated_reason')
              IS DISTINCT FROM (to_jsonb(OLD) - 'state' - 'invalidated_reason') THEN
            RAISE EXCEPTION 'saved inspection is immutable'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER inspection_immutable
BEFORE UPDATE OR DELETE ON inspection
FOR EACH ROW EXECUTE FUNCTION inspection_forbid_change();
"""

REVERSE = """
DROP TRIGGER IF EXISTS inspection_immutable ON inspection;
DROP FUNCTION IF EXISTS inspection_forbid_change();
ALTER TABLE inspection
    DROP CONSTRAINT IF EXISTS inspection_place_location_same_company_fk,
    DROP CONSTRAINT IF EXISTS inspection_rental_same_company_fk,
    DROP CONSTRAINT IF EXISTS inspection_vehicle_card_same_company_fk;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("inspections", "0001_initial"),
        ("rentals", "0006_rental_links_same_company"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE),
    ]
