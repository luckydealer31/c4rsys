"""The full export of the company's data (ТЗ § 22.6.1, § 2.8.1; decision Б-6).

* One ZIP: a CSV file per table with the identifiers of the records and of the related records
  (columns `*_id`), and a folder `documents/` with the files of the documents the employee may
  view (§ 18.5.4). A CSV is UTF-8 with a header line; JSON values are written as JSON text.
* The tables are every table of the company (the rows of this company only), the company
  profile, the global vehicles of its cards with their plates, the answers of the owners to its
  requests and the ownership recorded by them, and the names of its employees (§ 5.1.7).
* Secrets are never written: hashes of codes, tokens and passwords.
* The right is «Full data export» for the whole company; the link works 24 hours and checks the
  sign-in and the current right when opened (§ 2.8.1).
"""

import csv
import io
import json
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from django.apps import apps
from django.conf import settings
from django.db import models
from django.utils import timezone

from c4rsys.companies.models import Company
from c4rsys.exports.models import DataExport
from c4rsys.platform.commands import Actor, CommandContext
from c4rsys.platform.models import AuditEvent

PERMISSION = "data.export"
TTL = timedelta(hours=24)
SECRET_PARTS = ("hash", "token", "password", "secret")
# Technical tables without company data of their own.
SKIPPED = {"data_export", "idempotency_record", "notification_journal_cursor"}


def _root() -> Path:
    return Path(settings.C4R_FILES_ROOT) / "exports"


def path_of(export: DataExport) -> Path:
    return _root() / f"{export.id}.zip"


def _value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _columns(model: type[models.Model]) -> list[str]:
    return [
        f.attname
        for f in model._meta.concrete_fields
        if not any(part in f.attname for part in SECRET_PARTS)
    ]


def _csv(model: type[models.Model], rows: models.QuerySet[Any]) -> tuple[bytes, int]:
    columns = _columns(model)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(columns)
    count = 0
    for row in rows.order_by("pk").values_list(*columns):
        writer.writerow([_value(v) for v in row])
        count += 1
    return out.getvalue().encode("utf-8"), count


def _company_tables(company: Company) -> list[tuple[str, type[models.Model], Any]]:
    """Every table with a `company` column, filtered to the company, plus the related global
    records of § 22.6.1 (vehicles, plates, owners' answers and ownership, employees' names)."""
    found: list[tuple[str, type[models.Model], Any]] = []
    for model in apps.get_models():
        if not model.__module__.startswith("c4rsys.") or model._meta.db_table in SKIPPED:
            continue
        names = {f.name for f in model._meta.concrete_fields}
        if "company" in names:
            rows = model._default_manager.filter(company_id=company.id)
            found.append((model._meta.db_table, model, rows))
    found.append(("company", Company, Company.objects.filter(pk=company.id)))
    found.append(("audit_event", AuditEvent, AuditEvent.objects.filter(company_id=company.id)))

    vehicle = apps.get_model("fleet", "Vehicle")
    plate = apps.get_model("fleet", "VehiclePlate")
    answer = apps.get_model("fleet", "VehicleOwnerAnswer")
    ownership = apps.get_model("fleet", "VehicleOwnership")
    card = apps.get_model("fleet", "VehicleCard")
    vehicle_ids = card.objects.filter(company_id=company.id).values_list("vehicle_id", flat=True)
    found.append(("vehicle", vehicle, vehicle.objects.filter(pk__in=vehicle_ids)))
    found.append(("vehicle_plate", plate, plate.objects.filter(vehicle_id__in=vehicle_ids)))
    found.append(
        ("vehicle_owner_answer", answer, answer.objects.filter(request__company_id=company.id))
    )
    found.append(
        ("vehicle_ownership", ownership, ownership.objects.filter(request__company_id=company.id))
    )
    return sorted(found, key=lambda item: item[0])


def _employees(company: Company) -> bytes:
    """The names the company sees of its employees (§ 5.1.7): first and last name always,
    middle name and email only when the employee gave them."""
    from c4rsys.accounts import services as accounts
    from c4rsys.companies.models import Employment

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(
        ["employment_id", "user_public_id", "first_name", "last_name", "middle_name", "email"]
    )
    for e in (
        Employment.objects.select_related("user")
        .filter(company_id=company.id, user__isnull=False)
        .order_by("pk")
    ):
        user = e.user
        if user is None:
            continue
        emails = accounts.confirmed_emails(user) if e.shares_email else []
        writer.writerow(
            [
                str(e.id),
                user.public_id,
                user.first_name,
                user.last_name,
                user.middle_name if e.shares_middle_name else "",
                emails[0] if emails else "",
            ]
        )
    return out.getvalue().encode("utf-8")


def purge_expired(now: datetime | None = None) -> int:
    """Files of expired exports are removed; the record stays in the list."""
    moment = now or timezone.now()
    removed = 0
    for export in DataExport.objects.filter(expires_at__lte=moment, removed_at__isnull=True):
        path_of(export).unlink(missing_ok=True)
        export.removed_at = moment
        export.save(update_fields=["removed_at"])
        removed += 1
    return removed


def create(ctx: CommandContext, company: Company, actor: Actor) -> DataExport:
    from c4rsys.documents import services as documents

    purge_expired()
    now = timezone.now()
    export = DataExport(
        company=company, created_by_user_id=ctx.actor.user_id, created_at=now, expires_at=now + TTL
    )
    _root().mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    files = 0
    with zipfile.ZipFile(path_of(export), "w", compression=zipfile.ZIP_DEFLATED) as package:
        for name, model, rows in _company_tables(company):
            content, count = _csv(model, rows)
            package.writestr(f"tables/{name}.csv", content)
            counts[name] = count
        package.writestr("tables/employee_names.csv", _employees(company))
        for path, content in documents.files_for_export(actor, company.id):
            package.writestr(f"documents/{path}", content)
            files += 1
    export.size = path_of(export).stat().st_size
    export.tables = counts
    export.documents = files
    export.save()
    ctx.audit(
        "data.exported",
        object_type="data_export",
        object_id=export.id,
        after={"tables": len(counts), "documents": files, "expires_at": export.expires_at},
    )
    return export


def journal_download(export: DataExport, actor: Actor) -> None:
    AuditEvent.objects.create(
        company_id=export.company_id,
        actor_user_id=actor.user_id,
        actor_employment_id=actor.employment_id,
        session_id=actor.session_id,
        action="data.export_downloaded",
        object_type="data_export",
        object_id=str(export.id),
    )


def payload(export: DataExport, now: datetime) -> dict[str, Any]:
    available = export.removed_at is None and export.expires_at > now
    return {
        "id": str(export.id),
        "created_at": export.created_at.isoformat(),
        "expires_at": export.expires_at.isoformat(),
        "available": available,
        "size": export.size,
        "tables": len(export.tables),
        "rows": sum(export.tables.values()),
        "documents": export.documents,
    }


def get(company_id: UUID, export_id: UUID) -> DataExport | None:
    return DataExport.objects.filter(company_id=company_id, pk=export_id).first()
