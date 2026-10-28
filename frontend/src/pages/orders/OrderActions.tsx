import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";

import { useApi } from "../../api/queries";
import type { Client, Order, RentalProposal } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { formatMoment, fromLocalInput } from "../../time";
import { clientName } from "../clients/ClientsPage";
import { useVehicleTariffs } from "../rentals/Booking";
import { ClientFinder } from "../rentals/IssuePage";
import { employeeName, useEmployees } from "../settings/EmployeesPage";
import { useLocations } from "../settings/LocationsPage";
import { useVehicles } from "../vehicles/VehiclesPage";

type Done = () => Promise<void>;
type OrderResponse = { order: Order };

export const editable = (order: Order) =>
  order.state === "preliminary" || order.state === "open" || order.state === "ready_to_close";

/** § 12.1.7 (decision З3): «Create the order» for a client or the company itself; «Save as
 * preliminary» to discuss the lines and their cost first. */
export function NewOrder() {
  const { t } = useTranslation();
  const company = useCompany();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [internal, setInternal] = useState(false);
  const [chosen, setChosen] = useState<Client | null>(null);
  const create = useCommand<Record<string, unknown>, OrderResponse>(`/c/${company.public_id}/orders`);
  const ready = internal || chosen !== null;

  async function save(preliminary: boolean) {
    const result = await create.run({ client_id: internal ? null : chosen?.id, internal, preliminary });
    if (result) {
      await queryClient.invalidateQueries({ queryKey: ["orders", company.public_id] });
      navigate(companyPath(company, `/orders/${result.order.id}`));
    }
  }

  return (
    <Card title={t("orders.new")}>
      <label className="small">
        <input type="checkbox" checked={internal} onChange={() => setInternal(!internal)} /> {t("orders.internal_order")}
      </label>
      {internal ? null : chosen ? (
        <p>
          <strong>{clientName(chosen)}</strong>{" "}
          <button type="button" className="secondary" onClick={() => setChosen(null)}>
            {t("rentals.change_client")}
          </button>
        </p>
      ) : (
        <ClientFinder onPick={setChosen} />
      )}
      <ErrorMessage error={create.error} />
      <div className="actions">
        <button type="button" disabled={create.pending || !ready} onClick={() => void save(false)}>
          {t("orders.create")}
        </button>
        <button type="button" className="secondary" disabled={create.pending || !ready} onClick={() => void save(true)}>
          {t("orders.save_preliminary")}
        </button>
      </div>
    </Card>
  );
}

/** § 12.2.2: «Form the order» — the same order becomes an open one; proposals become bookings. */
export function FormOrder({ order, onDone }: { order: Order; onDone: Done }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const form = useCommand<Record<string, unknown>, OrderResponse>(`/c/${company.public_id}/orders/${order.id}/form`);
  if (order.state !== "preliminary" || !can("order.edit")) return null;
  return (
    <Card title={t("orders.preliminary_title")}>
      <p className="muted small">{t("orders.preliminary_hint")}</p>
      <ErrorMessage error={form.error} />
      <div className="actions">
        <button
          type="button"
          disabled={form.pending}
          onClick={() => void form.run({ expected_version: order.version }).then((r) => (r ? onDone() : undefined))}
        >
          {t("orders.form")}
        </button>
      </div>
    </Card>
  );
}

/** § 12.1.10 (decision З7): the responsible employee coordinates the order; no rights by itself. */
export function ResponsibleField({ order, onDone }: { order: Order; onDone: Done }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const employees = useEmployees();
  const [editing, setEditing] = useState(false);
  const [employmentId, setEmploymentId] = useState(order.responsible_employment_id ?? "");
  const change = useCommand<Record<string, unknown>, OrderResponse>(
    `/c/${company.public_id}/orders/${order.id}/responsible`,
  );
  const list = employees.data?.employees ?? [];
  const current = list.find((e) => e.id === order.responsible_employment_id);
  const name = current ? employeeName(current, t) : "—";
  if (!editing || !editable(order) || !can("order.edit")) {
    return (
      <>
        {name}{" "}
        {editable(order) && can("order.edit") ? (
          <button type="button" className="secondary" onClick={() => setEditing(true)}>
            {t("orders.change_responsible")}
          </button>
        ) : null}
      </>
    );
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await change.run({ employment_id: employmentId, expected_version: order.version });
    if (result) {
      setEditing(false);
      await onDone();
    }
  }

  return (
    <form onSubmit={submit}>
      <select value={employmentId} onChange={(e) => setEmploymentId(e.target.value)}>
        <option value="">—</option>
        {list
          .filter((e) => e.state === "active")
          .map((e) => (
            <option key={e.id} value={e.id}>
              {employeeName(e, t)}
            </option>
          ))}
      </select>{" "}
      <button type="submit" disabled={change.pending || !employmentId}>
        {t("common.save")}
      </button>{" "}
      <button type="button" className="secondary" onClick={() => setEditing(false)}>
        {t("common.cancel")}
      </button>
      <ErrorMessage error={change.error} />
    </form>
  );
}

/** § 12.2.1 (decision З4): the whole order before its operations start, with a reason; payments
 * stay as an overpayment. */
export function CancelOrder({ order, onDone }: { order: Order; onDone: Done }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const [reason, setReason] = useState("");
  const cancel = useCommand<Record<string, unknown>, OrderResponse>(`/c/${company.public_id}/orders/${order.id}/cancel`);
  if (!editable(order) || !can("order.cancel")) return null;

  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await cancel.run({ reason, expected_version: order.version });
    if (result) {
      setReason("");
      await onDone();
    }
  }

  return (
    <Card title={t("orders.cancel_title")}>
      <p className="muted small">{t("orders.cancel_hint")}</p>
      <form onSubmit={submit}>
        <div className="row">
          <Field label={t("orders.cancel_reason")}>
            <input value={reason} onChange={(e) => setReason(e.target.value)} />
          </Field>
        </div>
        <ErrorMessage error={cancel.error} />
        <div className="actions">
          <button type="submit" className="danger" disabled={cancel.pending || !reason.trim()}>
            {t("orders.cancel")}
          </button>
        </div>
      </form>
    </Card>
  );
}

/** Rentals offered in a preliminary order (decision З2): vehicle, tariff and planned period with
 * the cost; an overlap with another operation of the vehicle is only a warning until forming. */
export function RentalProposals({ order, onDone }: { order: Order; onDone: Done }) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const queryClient = useQueryClient();
  const key = ["rental-proposals", company.public_id, order.id];
  const url = `/c/${company.public_id}/orders/${order.id}/rental-proposals`;
  const proposals = useApi<{ proposals: RentalProposal[] }>(key, url, order.state === "preliminary");
  const vehicles = useVehicles();
  const locations = useLocations();
  const [vehicleId, setVehicleId] = useState("");
  const [tariffId, setTariffId] = useState("");
  const [locationId, setLocationId] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const tariffs = useVehicleTariffs(vehicleId);
  const add = useCommand<Record<string, unknown>, { proposal: RentalProposal }>(url);
  const withdraw = useCommand<{ rental_id: string; expected_version: number }, { ok: boolean }>(
    (body) => `/c/${company.public_id}/rentals/${body.rental_id}/withdraw-proposal`,
  );
  if (order.state !== "preliminary" || order.is_internal) return null;
  const zone = (locations.data?.locations ?? []).find((l) => l.id === locationId)?.timezone ?? company.timezone ?? "UTC";
  const vehicleName = new Map((vehicles.data?.vehicles ?? []).map((v) => [v.id, `${v.name} · ${v.vehicle_id}`]));
  const rentable = (vehicles.data?.vehicles ?? []).filter((v) => v.contexts.includes("rental"));
  const may = can("rental.book");

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: key });
    await onDone();
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await add.run({
      vehicle_card_id: vehicleId,
      tariff_id: tariffId,
      issue_location_id: locationId,
      planned_start: fromLocalInput(start, zone),
      planned_end: fromLocalInput(end, zone),
    });
    if (result) {
      setVehicleId("");
      setTariffId("");
      await refresh();
    }
  }

  async function remove(proposal: RentalProposal) {
    const result = await withdraw.run({ rental_id: proposal.id, expected_version: proposal.version });
    if (result) await refresh();
  }

  const items = proposals.data?.proposals ?? [];
  return (
    <Card title={t("orders.proposals")}>
      {items.length === 0 ? <p className="muted">{t("orders.no_proposals")}</p> : null}
      {items.length ? (
        <table>
          <tbody>
            {items.map((p) => (
              <tr key={p.id}>
                <td>{vehicleName.get(p.vehicle_card_id) ?? "—"}</td>
                <td>{p.tariff_name ?? "—"}</td>
                <td className="small">
                  {formatMoment(p.planned_start, p.calc_timezone, i18n.language)} —{" "}
                  {formatMoment(p.planned_end, p.calc_timezone, i18n.language)}
                </td>
                <td>{p.estimate ? `${p.estimate.amount} ${p.estimate.currency}` : "—"}</td>
                <td>{p.conflicts.length ? <span className="alert warn">{t("orders.proposal_conflict")}</span> : null}</td>
                <td>
                  {may ? (
                    <button type="button" className="secondary" disabled={withdraw.pending} onClick={() => void remove(p)}>
                      {t("orders.withdraw_proposal")}
                    </button>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      <ErrorMessage error={withdraw.error} />
      {may ? (
        <form onSubmit={submit}>
          <h3>{t("orders.add_proposal")}</h3>
          <div className="row">
            <Field label={t("rentals.vehicle")}>
              <select
                value={vehicleId}
                onChange={(e) => {
                  setVehicleId(e.target.value);
                  setTariffId("");
                }}
              >
                <option value="">—</option>
                {rentable.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name} · {v.vehicle_id}
                  </option>
                ))}
              </select>
            </Field>
            <Field label={t("rentals.tariff")}>
              <select value={tariffId} onChange={(e) => setTariffId(e.target.value)}>
                <option value="">—</option>
                {(tariffs.data?.tariffs ?? []).map((x) => (
                  <option key={x.id} value={x.id}>
                    {x.name} · {x.currency}
                  </option>
                ))}
              </select>
            </Field>
            <Field label={t("rentals.issue_location")}>
              <select value={locationId} onChange={(e) => setLocationId(e.target.value)}>
                <option value="">—</option>
                {(locations.data?.locations ?? []).map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.name}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <div className="row">
            <Field label={t("bookings.planned_start")} hint={zone}>
              <input type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} />
            </Field>
            <Field label={t("rentals.planned_end")} hint={zone}>
              <input type="datetime-local" value={end} onChange={(e) => setEnd(e.target.value)} />
            </Field>
          </div>
          <ErrorMessage error={add.error} />
          <div className="actions">
            <button type="submit" disabled={add.pending || !vehicleId || !tariffId || !start || !end}>
              {t("orders.add_proposal")}
            </button>
          </div>
        </form>
      ) : null}
    </Card>
  );
}
