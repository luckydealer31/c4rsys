"""Control versions belong to a rental of the same company (A05) and are immutable (§ 13.6.8)."""

from django.db import migrations

FORWARD = """
ALTER TABLE rental_calculation
    ADD CONSTRAINT rental_calculation_rental_same_company_fk
    FOREIGN KEY (company_id, rental_id) REFERENCES rental (company_id, id);

CREATE FUNCTION rental_calculation_forbid_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'rental_calculation is append-only'
        USING ERRCODE = 'insufficient_privilege';
END;
$$;
CREATE TRIGGER rental_calculation_append_only
BEFORE UPDATE OR DELETE ON rental_calculation
FOR EACH ROW EXECUTE FUNCTION rental_calculation_forbid_change();
"""

REVERSE = """
DROP TRIGGER IF EXISTS rental_calculation_append_only ON rental_calculation;
DROP FUNCTION IF EXISTS rental_calculation_forbid_change();
ALTER TABLE rental_calculation
    DROP CONSTRAINT IF EXISTS rental_calculation_rental_same_company_fk;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("rentals", "0009_rental_calculation"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE),
    ]
