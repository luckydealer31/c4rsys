"""HTTP API of reports and dashboards (ТЗ гл. 21, § 23.8.5; decisions О-1, О-2).

Viewing needs «View reports», the file needs «Export reports»; the data in both is the same
available set with the applied period, zone and filters (§ 21.1.1). The XLSX file is made here
with the labels of the interface in the user's language; PDF is the print view of the page.
"""

import io
import json
from collections.abc import Callable
from typing import Any
from uuid import UUID

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from openpyxl import Workbook

from c4rsys.accounts.middleware import require_user
from c4rsys.companies import access
from c4rsys.companies import services as companies
from c4rsys.companies.models import Company
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor
from c4rsys.reports import finance, overview, personal, rental, scope, service, warehouse

Builder = Callable[[Actor, Company, scope.Filters], list[overview.Block]]
DASHBOARDS: dict[str, Builder] = {
    "overview": overview.build,
    "rental": rental.build,
    "finance": finance.build,
    "service": service.build,
    "warehouse": warehouse.build,
}


def _actor(request: HttpRequest, cmp: str) -> tuple[companies.CompanyContext, Actor]:
    user, session = require_user(request)
    context = companies.resolve_company(user, cmp, session.id)
    return context, companies.company_actor(user, context, session.id)


def _require(actor: Actor, permission: str) -> None:
    if not access.RightsResolver().has_permission(actor, permission):
        raise errors.permission_denied(permission)


def _block(b: overview.Block, names: dict[Any, str]) -> dict[str, Any]:
    return {
        "code": b.code,
        "section": b.section,
        "unit": b.unit,
        "value": b.value,
        "rows": b.rows,
        "area": b.area.payload(names) if b.area is not None else None,
        "accounts": b.accounts,
        "warehouses": b.warehouses,
        "link": b.link,
    }


def _report(request: HttpRequest, cmp: str, code: str, permission: str) -> dict[str, Any]:
    context, actor = _actor(request, cmp)
    _require(actor, permission)
    builder = DASHBOARDS.get(code)
    if code.startswith("p-"):
        # A personal dashboard of the employee (§ 21.2.4).
        try:
            builder = personal.builder_for(UUID(code[2:]), DASHBOARDS)
        except ValueError as exc:
            raise errors.not_found() from exc
    if builder is None:
        raise errors.not_found()
    filters = scope.parse(request.GET, context.company)
    names = scope.location_names(context.company)
    blocks = builder(actor, context.company, filters)
    return {
        "dashboard": code,
        "filters": filters.payload(),
        "main_currency": context.company.main_currency_id,
        "blocks": [_block(b, names) for b in blocks],
    }


def _cell(value: Any) -> Any:
    """Numbers as numbers, the rest as text."""
    if isinstance(value, bool | int | float) or value is None:
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return str(value)


@require_GET
def dashboard(request: HttpRequest, cmp: str, code: str) -> JsonResponse:
    return JsonResponse(_report(request, cmp, code, "reports.view"))


@require_POST
def dashboard_xlsx(request: HttpRequest, cmp: str, code: str) -> HttpResponse:
    """`{labels: {code: text}, headers: {…}}` from the interface; the period and filters are in
    the query, as for viewing (§ 21.2.9)."""
    report = _report(request, cmp, code, "reports.export")
    try:
        body = json.loads(request.body or b"{}")
    except ValueError as exc:
        raise errors.validation_error("body") from exc
    labels = body.get("labels") if isinstance(body, dict) else None
    headers = body.get("headers") if isinstance(body, dict) else None
    labels = labels if isinstance(labels, dict) else {}
    headers = headers if isinstance(headers, dict) else {}

    def label(key: str) -> str:
        value = labels.get(key) or headers.get(key) or key
        return str(value)[:200]

    book = Workbook()
    sheet = book.active
    assert sheet is not None  # noqa: S101 - a new workbook always has its sheet
    sheet.title = "Report"
    f = report["filters"]
    sheet.append([label(code)])
    sheet.append([label("period"), f["start"], f["end"]])
    sheet.append([label("zone"), f["zone"]])
    if f["location_id"]:
        sheet.append([label("location"), str(headers.get("location_name", f["location_id"]))])
    if f["vehicle_id"]:
        sheet.append([label("vehicle"), str(headers.get("vehicle_name", f["vehicle_id"]))])
    sheet.append([])
    for b in report["blocks"]:
        area = b["area"]
        where = (
            label("whole_company")
            if (area and area["whole"])
            or (b["accounts"] is None and not b["warehouses"] and not area)
            else ", ".join(b["accounts"] or b["warehouses"] or (area["locations"] if area else []))
        )
        if b["unit"] == "table":
            sheet.append([label(b["code"]), where])
            if not b["rows"]:
                sheet.append(["", label("no_data")])
                continue
            keys = [k for k in b["rows"][0] if not k.endswith("_id")]
            sheet.append(["", *[label(f"col_{k}") for k in keys]])
            for row in b["rows"]:
                sheet.append(["", *[_cell(row.get(k)) for k in keys]])
            continue
        if b["unit"] in ("hours", "percent"):
            value = b["value"]
            sheet.append(
                [label(b["code"]), float(value) if value is not None else label("no_data"), where]
            )
            continue
        if b["unit"] == "count":
            sheet.append(
                [
                    label(b["code"]),
                    b["value"] if b["value"] is not None else label("no_data"),
                    where,
                ]
            )
        elif b["unit"] == "money":
            if not b["rows"]:
                sheet.append([label(b["code"]), 0, where])
            for row in b["rows"]:
                sheet.append([label(b["code"]), float(row["amount"]), row["currency"], where])
        else:
            total = b["value"] or {}
            sheet.append(
                [label(b["code"]), float(total.get("amount", 0)), total.get("currency"), where]
            )
            for row in b["rows"]:
                sheet.append(["", row["name"], float(row["balance"]), row["currency"]])
    out = io.BytesIO()
    book.save(out)
    response = HttpResponse(
        out.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{code}-{f["start"]}-{f["end"]}.xlsx"'
    return response


# --- Personal dashboards (§ 21.2.4–21.2.5) -------------------------------------------------------


def _body(request: HttpRequest) -> dict[str, Any]:
    try:
        data = json.loads(request.body or b"{}")
    except ValueError as exc:
        raise errors.validation_error("body") from exc
    if not isinstance(data, dict):
        raise errors.validation_error("body")
    return data


def _metrics(data: dict[str, Any]) -> list[str]:
    value = data.get("metrics") or []
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise errors.validation_error("metrics")
    return value


@require_http_methods(["GET", "POST"])
def personal_list(request: HttpRequest, cmp: str) -> JsonResponse:
    from c4rsys.companies.views import run_and_respond
    from c4rsys.platform.commands import CommandContext, CommandResult

    context, actor = _actor(request, cmp)
    _require(actor, "reports.view")
    if request.method == "GET":
        return JsonResponse(
            {
                "dashboards": [personal.payload(d) for d in personal.dashboards_of(actor)],
                "catalog": personal.catalog(),
            }
        )
    data = _body(request)
    name = str(data.get("name", ""))
    metrics = _metrics(data)

    def handler(ctx: CommandContext) -> CommandResult:
        found = personal.save(ctx, context.company, None, name, metrics, None)
        return CommandResult(201, {"dashboard": personal.payload(found)})

    payload = {"name": name, "metrics": metrics}
    return run_and_respond(request, "report.dashboard_create", actor, payload, handler)


@require_http_methods(["PUT", "DELETE"])
def personal_detail(request: HttpRequest, cmp: str, dashboard_id: UUID) -> JsonResponse:
    from c4rsys.companies.views import run_and_respond
    from c4rsys.platform.commands import CommandContext, CommandResult

    context, actor = _actor(request, cmp)
    _require(actor, "reports.view")
    personal.own(actor, dashboard_id)
    if request.method == "DELETE":

        def remove(ctx: CommandContext) -> CommandResult:
            personal.remove(ctx, dashboard_id)
            return CommandResult(200, {"removed": str(dashboard_id)})

        removing = {"dashboard_id": str(dashboard_id)}
        return run_and_respond(request, "report.dashboard_remove", actor, removing, remove)
    data = _body(request)
    name = str(data.get("name", ""))
    metrics = _metrics(data)
    expected = data.get("expected_version")
    if isinstance(expected, bool) or not isinstance(expected, int):
        raise errors.validation_error("expected_version", code="INTEGER_REQUIRED")

    def handler(ctx: CommandContext) -> CommandResult:
        found = personal.save(ctx, context.company, dashboard_id, name, metrics, expected)
        return CommandResult(200, {"dashboard": personal.payload(found)})

    payload: dict[str, Any] = {
        "dashboard_id": str(dashboard_id),
        "name": name,
        "metrics": metrics,
        "v": expected,
    }
    return run_and_respond(request, "report.dashboard_change", actor, payload, handler)
