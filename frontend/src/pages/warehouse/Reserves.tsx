import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { useApi } from "../../api/queries";
import type { PartInstallation, Storage, StockReserve, Vehicle, VehicleMaterialCosts } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { useReasonName, useReasons } from "./Operations";
import { StockAdder } from "./Sales";
import { useQuantity } from "./Stock";

function useWarehouses() {
  const company = useCompany();
  const storages = useApi<{ storages: Storage[] }>(
    ["warehouse-storages", company.public_id],
    `/c/${company.public_id}/warehouse/storages`,
  );
  return (storages.data?.storages ?? []).filter((s) => s.state === "enabled" && !s.location_deactivated);
}

function useRefreshWarehouse() {
  const client = useQueryClient();
  const company = useCompany();
  return async () => {
    for (const key of ["warehouse-reserves", "warehouse-removed-parts", "warehouse-stock", "warehouse-sources", "vehicle-parts"]) {
      await client.invalidateQueries({ queryKey: [key, company.public_id] });
    }
  };
}

/** Separate warehouse reserves for a definite operation (§ 11.7.1): the reserved stays in the
 * warehouse and is unavailable to other operations; a term is optional, past it an unused
 * reserve is released automatically. Reserves of works are made and released in the work. */
export function ReservesList() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const quantity = useQuantity();
  const refresh = useRefreshWarehouse();
  const [state, setState] = useState("active");
  const [comment, setComment] = useState("");
  const [expires, setExpires] = useState("");
  const [adding, setAdding] = useState(false);
  const reserves = useApi<{ reserves: StockReserve[] }>(
    ["warehouse-reserves", company.public_id, state],
    `/c/${company.public_id}/warehouse/reserves?state=${state}`,
    can("warehouse.view"),
  );
  const release = useCommand<{ id: string; reason: string }, unknown>(
    (b) => `/c/${company.public_id}/warehouse/reserves/${b.id}/release`,
  );
  if (!can("warehouse.view")) return <p className="muted">{t("warehouse.no_access")}</p>;
  return (
    <>
      {can("warehouse.reserve") ? (
        adding ? (
          <Card title={t("reserves.new")}>
            <div className="row">
              <Field label={t("reserves.comment")} hint={t("reserves.comment_hint")}>
                <input value={comment} onChange={(e) => setComment(e.target.value)} />
              </Field>
              <Field label={t("reserves.expires_at")} hint={t("reserves.expires_hint")}>
                <input type="datetime-local" value={expires} onChange={(e) => setExpires(e.target.value)} />
              </Field>
            </div>
            <StockAdder
              path={`/c/${company.public_id}/warehouse/reserves`}
              title={t("reserves.what")}
              button={t("reserves.reserve")}
              extra={{ comment, expires_at: expires ? new Date(expires).toISOString() : null }}
              onAdded={async (result) => {
                if (result !== null) {
                  setAdding(false);
                  setComment("");
                  setExpires("");
                  await refresh();
                }
              }}
            />
          </Card>
        ) : (
          <div className="actions" style={{ marginTop: 0, marginBottom: 16 }}>
            <button type="button" onClick={() => setAdding(true)}>
              {t("reserves.new")}
            </button>
          </div>
        )
      ) : null}
      <Card>
        <Field label={t("reserves.state")}>
          <select value={state} onChange={(e) => setState(e.target.value)}>
            {["active", "used", "released", "expired"].map((s) => (
              <option key={s} value={s}>
                {t(`reserves.state_${s}`)}
              </option>
            ))}
          </select>
        </Field>
        {reserves.isLoading ? <Loading /> : null}
        {reserves.data?.reserves.length === 0 ? <p className="muted">{t("reserves.none")}</p> : null}
        {reserves.data?.reserves.length ? (
          <table>
            <thead>
              <tr>
                <th>№</th>
                <th>{t("warehouse.name")}</th>
                <th>{t("operations.what")}</th>
                <th>{t("operations.quantity")}</th>
                <th>{t("reserves.purpose")}</th>
                <th>{t("reserves.expires_at")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {reserves.data.reserves.map((r) => (
                <tr key={r.id}>
                  <td>{r.number}</td>
                  <td>
                    <Link to={companyPath(company, `/warehouse/items/${r.item.id}`)}>{r.item.name}</Link>
                    <div className="small muted">{r.context.location_name}</div>
                  </td>
                  <td className="small">
                    {r.asset?.public_id ?? r.lot?.public_id}
                    {r.packaging_quantity && !r.opened_package_id ? ` · ${t("operations.pack_of", { size: quantity(r.packaging_quantity, r.item.unit) })}` : ""}
                    {r.opened_package_id ? ` · ${t("operations.opened")}` : ""}
                  </td>
                  <td>
                    {quantity(r.quantity, r.item.unit)}
                    {Number(r.used_quantity) ? <div className="small">{t("reserves.used", { value: quantity(r.used_quantity, r.item.unit) })}</div> : null}
                  </td>
                  <td className="small">{r.purpose === "work" ? t("reserves.purpose_work") : r.comment}</td>
                  <td className="small">{r.expires_at ? new Date(r.expires_at).toLocaleString() : t("reserves.no_term")}</td>
                  <td>
                    {r.state === "active" && r.purpose === "manual" && can("warehouse.reserve") ? (
                      <button
                        type="button"
                        className="link"
                        disabled={release.pending}
                        onClick={async () => {
                          if ((await release.run({ id: r.id, reason: "" })) !== null) await refresh();
                        }}
                      >
                        {t("reserves.release")}
                      </button>
                    ) : (
                      <span className="badge">{t(`reserves.state_${r.state}`)}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
        <ErrorMessage error={release.error} />
      </Card>
      <RemovedParts />
    </>
  );
}

/** Parts removed from vehicles awaiting a decision: into a warehouse with a new valuation — a
 * cost, an explicit zero or unknown — or a write-off (§ 11.4.7). */
export function RemovedParts() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const warehouses = useWarehouses();
  const refresh = useRefreshWarehouse();
  const reasons = useReasons();
  const reasonName = useReasonName();
  const parts = useApi<{ installations: PartInstallation[] }>(
    ["warehouse-removed-parts", company.public_id],
    `/c/${company.public_id}/warehouse/removed-parts`,
  );
  const [chosen, setChosen] = useState("");
  const [contextId, setContextId] = useState("");
  const [mode, setMode] = useState("unknown");
  const [cost, setCost] = useState("");
  const [reasonId, setReasonId] = useState("");
  const [comment, setComment] = useState("");
  const accept = useCommand<Record<string, unknown>, unknown>((b) => `/c/${company.public_id}/warehouse/removed-parts/${String(b.id)}/accept`);
  const writeOff = useCommand<Record<string, unknown>, unknown>((b) => `/c/${company.public_id}/warehouse/removed-parts/${String(b.id)}/write-off`);
  const found = parts.data?.installations ?? [];
  if (!found.length) return null;
  async function done(result: unknown) {
    if (result !== null) {
      setChosen("");
      setCost("");
      setComment("");
      await refresh();
    }
  }
  return (
    <Card title={t("parts.removed_title")}>
      <p className="muted small">{t("parts.removed_hint")}</p>
      <table>
        <tbody>
          {found.map((p) => (
            <tr key={p.id}>
              <td>
                {p.item.name} <span className="muted">{p.asset.public_id}</span>
                <div className="small">
                  {t("parts.removed_from", { vehicle: p.vehicle.name, at: p.removed_at ? new Date(p.removed_at).toLocaleString() : "" })}
                </div>
              </td>
              <td>
                <button type="button" className="link" onClick={() => setChosen(chosen === p.id ? "" : p.id)}>
                  {t("parts.decide")}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {chosen ? (
        <div className="line-card">
          <div className="row">
            <Field label={t("receipts.warehouse")}>
              <select value={contextId} onChange={(e) => setContextId(e.target.value)}>
                <option value="">—</option>
                {warehouses.map((w) => (
                  <option key={w.context_id} value={w.context_id}>
                    {w.location_name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label={t("parts.valuation")} hint={t("parts.valuation_hint")}>
              <select value={mode} onChange={(e) => setMode(e.target.value)}>
                <option value="value">{t("parts.cost_value")}</option>
                <option value="zero">{t("parts.cost_zero")}</option>
                <option value="unknown">{t("parts.cost_unknown")}</option>
              </select>
            </Field>
            {mode === "value" ? (
              <Field label={`${t("parts.cost")}, ${company.main_currency ?? ""}`}>
                <input inputMode="decimal" value={cost} onChange={(e) => setCost(e.target.value)} />
              </Field>
            ) : null}
          </div>
          <Field label={t("reserves.comment")}>
            <input value={comment} onChange={(e) => setComment(e.target.value)} />
          </Field>
          <div className="actions">
            {can("warehouse.receive") ? (
              <button
                type="button"
                disabled={!contextId || accept.pending || (mode === "value" && !cost)}
                onClick={async () =>
                  done(await accept.run({ id: chosen, context_id: contextId, cost_mode: mode, cost: mode === "value" ? cost : null, comment }))
                }
              >
                {t("parts.accept")}
              </button>
            ) : null}
            {can("warehouse.write_off") ? (
              <>
                <select value={reasonId} onChange={(e) => setReasonId(e.target.value)} aria-label={t("operations.reason")}>
                  <option value="">{t("operations.reason")}</option>
                  {(reasons.data?.reasons ?? []).filter((r) => r.state === "active").map((r) => (
                    <option key={r.id} value={r.id}>
                      {reasonName(r)}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  className="secondary"
                  disabled={!reasonId || writeOff.pending}
                  onClick={async () => done(await writeOff.run({ id: chosen, reason_id: reasonId, comment }))}
                >
                  {t("parts.write_off")}
                </button>
              </>
            ) : null}
          </div>
          <ErrorMessage error={accept.error ?? writeOff.error} />
        </div>
      ) : null}
    </Card>
  );
}

/** The parts installed on a vehicle with the periods of installation, and the costs of the
 * materials consumed on it with the mark «incomplete» (§ 11.4.3, § 11.4.4, § 11.2.9). */
export function VehicleParts({ vehicle }: { vehicle: Vehicle }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const quantity = useQuantity();
  const enabled = can("warehouse.view");
  const parts = useApi<{ installations: PartInstallation[] }>(
    ["vehicle-parts", company.public_id, vehicle.id],
    `/c/${company.public_id}/warehouse/vehicles/${vehicle.id}/parts`,
    enabled,
  );
  const costs = useApi<VehicleMaterialCosts>(
    ["vehicle-material-costs", company.public_id, vehicle.id],
    `/c/${company.public_id}/warehouse/vehicles/${vehicle.id}/material-costs`,
    enabled,
  );
  if (!enabled) return null;
  const periods = parts.data?.installations ?? [];
  const lines = costs.data?.lines ?? [];
  const unit = vehicle.odometer_unit ?? "km";
  return (
    <Card title={t("parts.vehicle_title")}>
      {periods.length === 0 ? <p className="muted">{t("parts.none")}</p> : null}
      {periods.length ? (
        <table>
          <thead>
            <tr>
              <th>{t("parts.part")}</th>
              <th>{t("parts.installed")}</th>
              <th>{t("parts.removed")}</th>
              <th>{t("parts.run")}</th>
            </tr>
          </thead>
          <tbody>
            {periods.map((p) => (
              <tr key={p.id}>
                <td>
                  {p.item.name} <span className="muted">{p.asset.public_id}</span>
                </td>
                <td className="small">
                  {new Date(p.installed_at).toLocaleDateString()}
                  {p.installed_odometer ? ` · ${Number(p.installed_odometer)} ${t(`vehicles.unit_${unit}`)}` : ""}
                </td>
                <td className="small">
                  {p.removed_at ? (
                    <>
                      {new Date(p.removed_at).toLocaleDateString()}
                      {p.removed_odometer ? ` · ${Number(p.removed_odometer)} ${t(`vehicles.unit_${unit}`)}` : ""}
                      {p.replaced_by ? <div>{t("parts.replaced")}</div> : null}
                    </>
                  ) : (
                    <span className="badge ok">{t("parts.on_vehicle")}</span>
                  )}
                </td>
                <td className="small">
                  {t("parts.days", { count: p.days })} ·{" "}
                  {p.distance === null ? t("parts.distance_unknown") : `${Number(p.distance)} ${t(`vehicles.unit_${unit}`)}`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      <h3>{t("parts.costs_title")}</h3>
      {lines.length === 0 ? <p className="muted">{t("parts.no_costs")}</p> : null}
      {lines.length ? (
        <>
          <table>
            <tbody>
              {lines.map((l) => (
                <tr key={l.line_id}>
                  <td className="small">{new Date(l.at).toLocaleDateString()}</td>
                  <td>
                    {l.item.name} <span className="muted small">{l.what}</span>
                  </td>
                  <td>{quantity(l.consumed, l.item.unit)}</td>
                  <td>
                    {l.cost} {costs.data?.currency}
                    {l.cost_known ? null : <span className="badge warn" style={{ marginLeft: 6 }}>{t("parts.incomplete")}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p>
            <strong>
              {t("parts.total")}: {costs.data?.total} {costs.data?.currency}
            </strong>
            {costs.data?.incomplete ? <span className="badge warn" style={{ marginLeft: 6 }}>{t("parts.incomplete")}</span> : null}
          </p>
        </>
      ) : null}
    </Card>
  );
}
