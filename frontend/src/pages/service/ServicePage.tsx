import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { useApi } from "../../api/queries";
import type { MaintenanceRule, QueueJob, ServiceOperation } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { formatMoment } from "../../time";
import { useLocations } from "../settings/LocationsPage";
import { dueText } from "./VehicleService";
import {
  FINAL_STATES,
  HOLDING_STATES,
  OPERATION_KEYS,
  serviceLocations,
  useServiceOperations,
  useServicePosts,
  useServiceQueue,
  zoneOf,
} from "./hooks";

/** The «Service» section by service location: posts, the general zone and planned operations
 * (§ 16.3), the queue of free works for a mechanic (§ 23.2.4; decision С-7), all operations. */
export function ServicePage() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const locations = useLocations();
  const [all, setAll] = useState(false);
  const operations = useServiceOperations(all ? "" : "active=1");
  const places = serviceLocations(locations.data?.locations);
  return (
    <div className="page">
      <h1>{t("nav.service")}</h1>
      <div className="actions" style={{ marginBottom: 16, marginTop: 0 }}>
        {can("service.create") ? (
          <Link className="button" to={companyPath(company, "/service/operations/new")}>
            {t("service.new_operation")}
          </Link>
        ) : null}
        {can("service.view") || can("service.catalog") ? (
          <Link className="button secondary" to={companyPath(company, "/service/works")}>
            {t("service.catalog")}
          </Link>
        ) : null}
      </div>
      {locations.isLoading || operations.isLoading ? <Loading /> : null}
      {places.length === 0 && locations.data ? <p className="muted">{t("service.no_locations")}</p> : null}
      {can("vehicle.view") ? <MaintenanceDue /> : null}
      {can("service.work") ? <Queue /> : null}
      {places.map((l) => (
        <Board key={l.id} locationId={l.id} name={l.name} operations={operations.data?.operations ?? []} />
      ))}
      <Card title={t("service.operations")}>
        <label className="small">
          <input type="checkbox" checked={all} onChange={() => setAll(!all)} /> {t("service.show_finished")}
        </label>
        <OperationTable items={operations.data?.operations ?? []} />
      </Card>
    </div>
  );
}

function Board({ locationId, name, operations }: { locationId: string; name: string; operations: ServiceOperation[] }) {
  const { t } = useTranslation();
  const posts = useServicePosts();
  const here = operations.filter((o) => o.location_id === locationId);
  const held = here.filter((o) => HOLDING_STATES.includes(o.state));
  const planned = here.filter((o) => o.state === "planned");
  const active = (posts.data?.posts ?? []).filter((p) => p.location_id === locationId && p.state === "active");
  return (
    <Card title={`${t("contexts.service")} — ${name}`}>
      <div className="tiles">
        {active.map((p) => {
          const on = held.find((o) => o.post_id === p.id);
          return (
            <div key={p.id} className="list-link">
              <strong>{p.name}</strong>
              <div className="small">{on ? <OperationLink op={on} /> : <span className="muted">{t("service.post_free")}</span>}</div>
            </div>
          );
        })}
        <div className="list-link">
          <strong>{t("service.general_zone")}</strong>
          {held.filter((o) => !o.post_id).length === 0 ? <div className="muted small">{t("service.zone_empty")}</div> : null}
          {held
            .filter((o) => !o.post_id)
            .map((o) => (
              <div key={o.id} className="small">
                <OperationLink op={o} />
              </div>
            ))}
        </div>
      </div>
      {planned.length ? (
        <>
          <h3 style={{ marginTop: 12 }}>{t("service.planned")}</h3>
          <OperationTable items={planned} />
        </>
      ) : null}
    </Card>
  );
}

function OperationLink({ op }: { op: ServiceOperation }) {
  const { t } = useTranslation();
  const company = useCompany();
  return (
    <Link to={companyPath(company, `/service/operations/${op.id}`)}>
      № {op.number} · {op.vehicle_name ?? "—"} · {t(`service.state_${op.state}`)}
    </Link>
  );
}

function OperationTable({ items }: { items: ServiceOperation[] }) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const locations = useLocations();
  if (items.length === 0) return <p className="muted">{t("service.no_operations")}</p>;
  return (
    <table>
      <tbody>
        {items.map((op) => {
          const location = locations.data?.locations.find((l) => l.id === op.location_id);
          const zone = zoneOf(location, company);
          return (
            <tr key={op.id}>
              <td>
                <Link to={companyPath(company, `/service/operations/${op.id}`)}>№ {op.number}</Link>
              </td>
              <td>{op.vehicle_name ?? "—"}</td>
              <td>{op.reason}</td>
              <td className="small">
                {op.planned_start ? formatMoment(op.planned_start, zone, i18n.language) : "—"}
                {op.post_name ? ` · ${op.post_name}` : ""}
              </td>
              <td>
                <span className={`badge ${FINAL_STATES.includes(op.state) ? "" : "warn"}`}>{t(`service.state_${op.state}`)}</span>
              </td>
              <td>
                <Link to={companyPath(company, `/orders/${op.order_id}`)}>
                  {t("service.order_number", { number: op.order_number })}
                </Link>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/** Free works of the employee's locations; «Take» makes them the executor (decision С-7). */
function Queue() {
  const { t } = useTranslation();
  const company = useCompany();
  const queue = useServiceQueue();
  const jobs = queue.data?.jobs ?? [];
  return (
    <Card title={t("service.queue")}>
      {queue.isLoading ? <Loading /> : null}
      {queue.data && jobs.length === 0 ? <p className="muted">{t("service.queue_empty")}</p> : null}
      {jobs.length ? (
        <table>
          <tbody>
            {jobs.map((job) => (
              <QueueRow key={job.id} job={job} path={companyPath(company, `/service/operations/${job.operation_id}`)} />
            ))}
          </tbody>
        </table>
      ) : null}
    </Card>
  );
}

function QueueRow({ job, path }: { job: QueueJob; path: string }) {
  const { t } = useTranslation();
  const company = useCompany();
  const queryClient = useQueryClient();
  const take = useCommand<Record<string, unknown>, unknown>(`/c/${company.public_id}/service/jobs/${job.id}/take`);
  return (
    <tr>
      <td>
        <Link to={path}>№ {job.operation_number}</Link>
      </td>
      <td>{job.vehicle_name ?? "—"}</td>
      <td>{job.name}</td>
      <td>
        <button
          type="button"
          className="secondary small"
          disabled={take.pending}
          onClick={() =>
            void take.run({ expected_version: job.version }).then(async (r) => {
              if (r) for (const k of OPERATION_KEYS) await queryClient.invalidateQueries({ queryKey: [k] });
            })
          }
        >
          {t("service.take")}
        </button>
        <ErrorMessage error={take.error} />
      </td>
    </tr>
  );
}

/** Vehicles whose maintenance is required or soon; it does not block a rental (§ 9.1.13). */
function MaintenanceDue() {
  const { t } = useTranslation();
  const company = useCompany();
  const due = useApi<{ due: (MaintenanceRule & { vehicle_name: string; unit: string })[] }>(
    ["maintenance-due", company.public_id],
    `/c/${company.public_id}/service/maintenance-due`,
  );
  const items = due.data?.due ?? [];
  if (items.length === 0) return null;
  return (
    <Card title={t("service.maintenance_due")}>
      <table>
        <tbody>
          {items.map((r) => (
            <tr key={r.id}>
              <td>
                <Link to={companyPath(company, `/vehicles/${r.vehicle_card_id}`)}>{r.vehicle_name}</Link>
              </td>
              <td>{r.name}</td>
              <td>
                <span className="badge warn">{t(`service.rule_${r.status.state}`)}</span>
              </td>
              <td className="small">{dueText(r, r.unit, t)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
