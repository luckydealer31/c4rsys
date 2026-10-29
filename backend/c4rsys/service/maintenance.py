"""Periodic maintenance of a vehicle and the history of its works (ТЗ гл. 9).

* A rule is linked to a system work, or to a work of the company's catalog, or has only its own
  name; without a link the execution is not recognized automatically (§ 9.1.5, § 9.1.14).
* The count starts from the last done work of the rule: its actual date and odometer (§ 9.1.11).
  Without one — from the last execution or the first coming service given by hand; the initial
  setting does not create a fictional work (§ 9.1.7). Without them the rule waits for the first
  done work (§ 9.1.8). Cancelled and terminated works do not restart it (§ 16.4.4).
* Distance and time together: required at the first of the two (§ 9.1.9); a reminder some days
  or distance before (§ 9.1.10). «Required» does not block a rental (§ 9.1.13).
* Changing an interval recounts from the kept base; it is not an execution (§ 9.1.15).
* Rules are copied without history: missing ones are added, a match is kept or replaced only by
  the employee's explicit choice (§ 9.1.12).

PROPOSAL (OPEN): rules are kept by «Change vehicles»; a rule matches another when it has the same
system work, the same catalog work or, without both, the same name; a rule is archived, not
deleted.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from django.utils import timezone

from c4rsys.companies import locations
from c4rsys.fleet import services as fleet
from c4rsys.fleet.models import VehicleCard
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.service.models import MaintenanceRule, ServiceJob, ServiceWork, SystemWork

Job = ServiceJob.State


def _today(card: VehicleCard) -> date:
    """Today in the time zone of the vehicle's location (§ 4.2.2), else of the server."""
    if card.accounting_location_id is not None:
        info = locations.location_info(card.company_id, card.accounting_location_id)
        if info is not None and info.timezone:
            return timezone.localdate(timezone=ZoneInfo(info.timezone))
    return timezone.localdate()


def done_jobs(company_id: UUID, card_id: UUID) -> list[ServiceJob]:
    """Works done or ended on the vehicle, latest first — the history of the vehicle (§ 9.1.2)."""
    return list(
        ServiceJob.objects.select_related("operation")
        .filter(
            company_id=company_id,
            operation__vehicle_card_id=card_id,
            state__in=(Job.DONE, Job.TERMINATED),
        )
        .order_by("-actual_end", "-id")
    )


def _matches(rule: MaintenanceRule, job: ServiceJob) -> bool:
    if job.state != Job.DONE:
        return False
    if rule.system_work_id is not None:
        return bool(job.snapshot.get("system_work") == rule.system_work_id)
    if rule.work_id is not None:
        return job.work_id == rule.work_id
    return False


@dataclass(frozen=True)
class Status:
    base: str  # "work", "manual_last", "manual_next" or "none"
    base_on: date | None
    base_odometer: Decimal | None
    due_on: date | None
    due_odometer: Decimal | None
    state: str  # "ok", "soon", "required", "unknown"
    last_job_id: UUID | None


def status_of(
    rule: MaintenanceRule, jobs: list[ServiceJob], odometer: Decimal | None, today: date
) -> Status:
    last = next((j for j in jobs if _matches(rule, j)), None)
    base_on: date | None
    base_odometer: Decimal | None
    due_on: date | None = None
    due_odometer: Decimal | None = None
    if last is not None and last.actual_end is not None:
        base, base_on, base_odometer = "work", timezone.localdate(last.actual_end), last.odometer
    elif rule.last_done_on is not None or rule.last_done_odometer is not None:
        base, base_on, base_odometer = "manual_last", rule.last_done_on, rule.last_done_odometer
    else:
        base, base_on, base_odometer = "none", None, None
    if base_on is not None and rule.interval_days:
        due_on = base_on + timedelta(days=rule.interval_days)
    if base_odometer is not None and rule.interval_distance:
        due_odometer = base_odometer + rule.interval_distance
    if base == "none":
        due_on, due_odometer = rule.next_due_on, rule.next_due_odometer
        if due_on is not None or due_odometer is not None:
            base = "manual_next"
    state = "unknown"
    if due_on is not None or due_odometer is not None:
        state = "ok"
        soon = False
        if due_on is not None:
            if today >= due_on:
                state = "required"
            elif rule.remind_days and today >= due_on - timedelta(days=rule.remind_days):
                soon = True
        if due_odometer is not None and odometer is not None:
            if odometer >= due_odometer:
                state = "required"
            elif rule.remind_distance and odometer >= due_odometer - rule.remind_distance:
                soon = True
        if state == "ok" and soon:
            state = "soon"
    return Status(
        base=base,
        base_on=base_on,
        base_odometer=base_odometer,
        due_on=due_on,
        due_odometer=due_odometer,
        state=state,
        last_job_id=last.id if last else None,
    )


def rules_of(company_id: UUID, card_id: UUID, *, archived: bool = False) -> list[MaintenanceRule]:
    query = MaintenanceRule.objects.filter(company_id=company_id, vehicle_card_id=card_id)
    if not archived:
        query = query.filter(state=MaintenanceRule.State.ACTIVE)
    return list(query.order_by("name", "id"))


def odometer_of(card_id: UUID) -> Decimal | None:
    reading = fleet.current_reading(card_id)
    return reading.value if reading else None


def statuses(company_id: UUID, card: VehicleCard) -> list[tuple[MaintenanceRule, Status]]:
    jobs = done_jobs(company_id, card.id)
    odometer = odometer_of(card.id)
    today = _today(card)
    return [(r, status_of(r, jobs, odometer, today)) for r in rules_of(company_id, card.id)]


def due(company_id: UUID) -> list[tuple[VehicleCard, MaintenanceRule, Status]]:
    """Rules of the company's vehicles that need service now or soon (§ 9.1.10, § 9.1.13)."""
    result = []
    cards = {
        r.vehicle_card_id
        for r in MaintenanceRule.objects.filter(
            company_id=company_id, state=MaintenanceRule.State.ACTIVE
        )
    }
    for card in VehicleCard.objects.filter(
        company_id=company_id, pk__in=cards, state=VehicleCard.State.ACTIVE
    ):
        for rule, status in statuses(company_id, card):
            if status.state in ("required", "soon"):
                result.append((card, rule, status))
    return result


# --- Commands -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleData:
    name: str
    system_work: str | None
    work_id: UUID | None
    interval_distance: int | None
    interval_days: int | None
    remind_distance: int | None
    remind_days: int | None
    last_done_on: date | None
    last_done_odometer: Decimal | None
    next_due_on: date | None
    next_due_odometer: Decimal | None


def _card(company_id: UUID, card_id: UUID) -> VehicleCard:
    """§ 9.1.17: the owner or the managing company keeps the rules of its vehicles."""
    card = fleet.lock_card(company_id, card_id)
    if card.relation not in (VehicleCard.Relation.OWN, VehicleCard.Relation.MANAGED):
        raise errors.business_rule("VEHICLE_NOT_OWNED_OR_MANAGED", field="vehicle_card_id")
    return card


def _validate(company_id: UUID, data: RuleData) -> None:
    if not data.interval_distance and not data.interval_days:
        raise errors.validation_error("interval_days", code="MAINTENANCE_INTERVAL_REQUIRED")
    if data.system_work is not None and not SystemWork.objects.filter(pk=data.system_work).exists():
        raise errors.validation_error("system_work", code="SYSTEM_WORK_UNKNOWN")
    if data.system_work is not None and data.work_id is not None:
        raise errors.validation_error("work_id", code="MAINTENANCE_ONE_LINK")
    if (
        data.work_id is not None
        and not ServiceWork.objects.filter(company_id=company_id, pk=data.work_id).exists()
    ):
        raise errors.validation_error("work_id", code="SERVICE_WORK_UNKNOWN")


def snapshot(rule: MaintenanceRule) -> dict[str, Any]:
    return {
        "name": rule.name,
        "system_work": rule.system_work_id,
        "work": str(rule.work_id) if rule.work_id else None,
        "interval_distance": rule.interval_distance,
        "interval_days": rule.interval_days,
        "remind_distance": rule.remind_distance,
        "remind_days": rule.remind_days,
        "last_done_on": rule.last_done_on,
        "last_done_odometer": str(rule.last_done_odometer)
        if rule.last_done_odometer is not None
        else None,
        "next_due_on": rule.next_due_on,
        "next_due_odometer": str(rule.next_due_odometer)
        if rule.next_due_odometer is not None
        else None,
        "state": rule.state,
    }


def _apply(rule: MaintenanceRule, data: RuleData) -> None:
    rule.name = data.name
    rule.system_work_id = data.system_work
    rule.work_id = data.work_id
    rule.interval_distance = data.interval_distance
    rule.interval_days = data.interval_days
    rule.remind_distance = data.remind_distance
    rule.remind_days = data.remind_days
    rule.last_done_on = data.last_done_on
    rule.last_done_odometer = data.last_done_odometer
    rule.next_due_on = data.next_due_on
    rule.next_due_odometer = data.next_due_odometer


def create_rule(
    ctx: CommandContext, company_id: UUID, card_id: UUID, data: RuleData
) -> MaintenanceRule:
    _card(company_id, card_id)
    _validate(company_id, data)
    rule = MaintenanceRule(
        company_id=company_id, vehicle_card_id=card_id, created_by_user_id=ctx.actor.user_id
    )
    _apply(rule, data)
    rule.save()
    ctx.audit(
        "maintenance_rule.created",
        object_type="maintenance_rule",
        object_id=rule.id,
        after={"vehicle": str(card_id), **snapshot(rule)},
    )
    return rule


def locked_rule(company_id: UUID, rule_id: UUID, expected_version: int) -> MaintenanceRule:
    rule = (
        MaintenanceRule.objects.select_for_update()
        .filter(company_id=company_id, pk=rule_id)
        .first()
    )
    if rule is None:
        raise errors.not_found()
    if rule.version != expected_version:
        raise errors.version_conflict()
    return rule


def change_rule(ctx: CommandContext, rule: MaintenanceRule, data: RuleData) -> MaintenanceRule:
    """New intervals count from the kept base; the change is not an execution (§ 9.1.15)."""
    _validate(rule.company_id, data)
    before = snapshot(rule)
    _apply(rule, data)
    after = snapshot(rule)
    if before == after:
        raise errors.business_rule("NOTHING_CHANGED")
    rule.version += 1
    rule.save()
    ctx.audit(
        "maintenance_rule.changed",
        object_type="maintenance_rule",
        object_id=rule.id,
        before={k: v for k, v in before.items() if v != after[k]},
        after={k: v for k, v in after.items() if v != before[k]},
    )
    return rule


def set_archived(ctx: CommandContext, rule: MaintenanceRule, archived: bool) -> MaintenanceRule:
    target = MaintenanceRule.State.ARCHIVED if archived else MaintenanceRule.State.ACTIVE
    if rule.state == target:
        raise errors.business_rule("NOTHING_CHANGED")
    rule.state = target
    rule.version += 1
    rule.save(update_fields=["state", "version", "updated_at"])
    ctx.audit(
        "maintenance_rule.archived" if archived else "maintenance_rule.restored",
        object_type="maintenance_rule",
        object_id=rule.id,
        after={"state": target},
    )
    return rule


def _same(a: MaintenanceRule, b: MaintenanceRule) -> bool:
    if a.system_work_id or b.system_work_id:
        return a.system_work_id == b.system_work_id
    if a.work_id or b.work_id:
        return a.work_id == b.work_id
    return a.name.strip().lower() == b.name.strip().lower()


def copy_rules(
    ctx: CommandContext,
    company_id: UUID,
    source_id: UUID,
    target_id: UUID,
    choices: dict[UUID, str],
) -> list[MaintenanceRule]:
    """Copies the active rules without the history and the initial data; a matching rule of the
    target is kept or replaced by `choices[source rule] = "keep" | "replace"` (§ 9.1.12)."""
    if source_id == target_id:
        raise errors.validation_error("from_vehicle_id", code="MAINTENANCE_SAME_VEHICLE")
    fleet.get_card(company_id, source_id)
    _card(company_id, target_id)
    existing = rules_of(company_id, target_id)
    conflicts = []
    plan: list[tuple[MaintenanceRule, MaintenanceRule | None]] = []
    for rule in rules_of(company_id, source_id):
        match = next((r for r in existing if _same(r, rule)), None)
        if match is not None and choices.get(rule.id) not in ("keep", "replace"):
            conflicts.append({"rule": str(rule.id), "name": rule.name, "existing": str(match.id)})
        plan.append((rule, match))
    if conflicts:
        raise errors.business_rule("MAINTENANCE_COPY_CONFLICTS", conflicts=conflicts)
    created = []
    for rule, match in plan:
        if match is not None and choices.get(rule.id) == "keep":
            continue
        data = RuleData(
            name=rule.name,
            system_work=rule.system_work_id,
            work_id=rule.work_id,
            interval_distance=rule.interval_distance,
            interval_days=rule.interval_days,
            remind_distance=rule.remind_distance,
            remind_days=rule.remind_days,
            last_done_on=None,
            last_done_odometer=None,
            next_due_on=None,
            next_due_odometer=None,
        )
        if match is not None:
            locked = locked_rule(company_id, match.id, match.version)
            set_archived(ctx, locked, True)
        created.append(create_rule(ctx, company_id, target_id, data))
    return created
