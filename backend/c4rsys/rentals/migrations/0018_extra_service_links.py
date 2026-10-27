"""Additional services and their lines belong to the same company as everything they point to;
a line of an order is never deleted: a mistaken one is cancelled before its start (§ 13.4.15,
§ 24.4.6)."""

from django.db import migrations

LINKS = [
    ("rental_extra_service_location", "service_id", "rental_extra_service"),
    ("rental_extra_service_location", "location_id", "location"),
    ("rental_extra_service_vehicle", "service_id", "rental_extra_service"),
    ("rental_extra_service_vehicle", "vehicle_card_id", "vehicle_card"),
    ("rental_extra_service_tariff", "service_id", "rental_extra_service"),
    ("rental_extra_service_tariff", "tariff_id", "tariff"),
    ("rental_extra_service_line", "order_id", "orders"),
    ("rental_extra_service_line", "service_id", "rental_extra_service"),
    ("rental_extra_service_line", "rental_id", "rental"),
    ("rental_extra_service_line", "origin_rental_id", "rental"),
    ("rental_extra_service_line", "location_id", "location"),
    ("rental_extra_service_free_interval", "line_id", "rental_extra_service_line"),
]

FORWARD = "".join(
    f"""
ALTER TABLE {table}
    ADD CONSTRAINT {table}_{column}_same_company_fk
    FOREIGN KEY (company_id, {column}) REFERENCES {target} (company_id, id);"""
    for table, column, target in LINKS
) + """
CREATE FUNCTION rental_extra_service_line_not_deleted() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'rental_extra_service_line is never deleted'
        USING ERRCODE = 'insufficient_privilege';
END;
$$;
CREATE TRIGGER rental_extra_service_line_protect
BEFORE DELETE ON rental_extra_service_line
FOR EACH ROW EXECUTE FUNCTION rental_extra_service_line_not_deleted();
"""

REVERSE = """
DROP TRIGGER IF EXISTS rental_extra_service_line_protect ON rental_extra_service_line;
DROP FUNCTION IF EXISTS rental_extra_service_line_not_deleted();
""" + "".join(
    f"""
ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {table}_{column}_same_company_fk;"""
    for table, column, _ in LINKS
)


class Migration(migrations.Migration):
    dependencies = [
        ("rentals", "0017_extra_services"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE),
    ]
