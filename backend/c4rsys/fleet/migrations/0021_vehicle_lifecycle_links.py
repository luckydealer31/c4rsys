"""Block 12, part Б: the links of the new tables to a card of the same company, the first period
of every existing card (§ 8.7.6) and the confirmed management of the earlier three «yes»
(§ 8.3.1)."""

from typing import Any

from django.db import migrations

TABLES = (
    "vehicle_card_period",
    "vehicle_disposal",
    "vehicle_transfer_reversal",
    "vehicle_restriction",
    "odometer_replacement",
    "odometer_unit_change",
    "vehicle_management_confirmation",
    "vehicle_disclosure_setting",
)

FORWARD = "\n".join(
    f"""ALTER TABLE {table}
    ADD CONSTRAINT {table}_card_same_company_fk
    FOREIGN KEY (company_id, vehicle_card_id) REFERENCES vehicle_card (company_id, id);"""
    for table in TABLES
) + """
ALTER TABLE vehicle_restriction
    ADD CONSTRAINT vehicle_restriction_reason_same_company_fk
    FOREIGN KEY (company_id, custom_reason_id) REFERENCES vehicle_restriction_reason (company_id, id);
"""

REVERSE = "\n".join(
    f"ALTER TABLE {table} DROP CONSTRAINT {table}_card_same_company_fk;" for table in TABLES
) + """
ALTER TABLE vehicle_restriction DROP CONSTRAINT vehicle_restriction_reason_same_company_fk;
"""

QUESTIONS = ("link", "ownership", "management")


def periods(apps: Any, schema_editor: Any) -> None:
    card = apps.get_model("fleet", "VehicleCard")
    period = apps.get_model("fleet", "VehicleCardPeriod")
    rows = []
    for c in card.objects.all():
        archived = c.state == "archived"
        rows.append(
            period(
                company_id=c.company_id,
                vehicle_card_id=c.id,
                relation=c.relation,
                started_at=c.created_at,
                ended_at=max(c.updated_at, c.created_at) if archived else None,
                start_basis="created",
                end_basis="archive" if archived else "",
                started_by_user_id=c.created_by_user_id,
            )
        )
    period.objects.bulk_create(rows)
    request = apps.get_model("fleet", "VehicleOwnerRequest")
    confirmation = apps.get_model("fleet", "VehicleManagementConfirmation")
    claim = apps.get_model("fleet", "VehicleOwnerClaim")
    for r in request.objects.filter(state="complete"):
        answers = {a.question: a for a in r.answers.all()}
        if not all(q in answers and answers[q].answer for q in QUESTIONS):
            continue
        latest = claim.objects.filter(vehicle_card_id=r.vehicle_card_id).order_by("-created_at")
        current = latest.first()
        newest = request.objects.filter(vehicle_card_id=r.vehicle_card_id).order_by("-created_at")
        is_current = current is not None and current.id == r.claim_id and newest.first().id == r.id
        confirmation.objects.create(
            company_id=r.company_id,
            vehicle_card_id=r.vehicle_card_id,
            vehicle_id=r.vehicle_id,
            request_id=r.id,
            owner_user_id=r.target_user_id if r.target_company_id is None else None,
            owner_company_id=r.target_company_id,
            started_at=max(a.answered_at for a in answers.values()),
            ended_at=None if is_current else (current.created_at if current else r.closed_at),
            end_reason="" if is_current else "owner_changed",
        )


class Migration(migrations.Migration):
    dependencies = [("fleet", "0020_vehicle_lifecycle")]

    operations = [
        migrations.RunSQL(FORWARD, REVERSE),
        migrations.RunPython(periods, migrations.RunPython.noop),
    ]
