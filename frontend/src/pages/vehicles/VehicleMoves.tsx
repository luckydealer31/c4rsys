import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { useApi } from "../../api/queries";
import type { Vehicle, VehicleMove } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { formatMoment, fromLocalInput, toLocalInput } from "../../time";
import { useLocations } from "../settings/LocationsPage";

/** Moves of the vehicle and recording a move that already happened, in one action: destination,
 * driver, actual start and end (§ 8.5.2, § 15.1.3–15.1.5, § 23.6.2; decision 23.10.2026). An
 * internal order of the company is created for it. */
export function VehicleMoves({ vehicle }: { vehicle: Vehicle }) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const locations = useLocations();
  const all = locations.data?.locations ?? [];
  const names = new Map(all.map((l) => [l.id, l.name]));
  // A move is shown in the time zone of the location it started from.
  const zones = new Map(all.map((l) => [l.id, l.timezone ?? company.timezone ?? "UTC"]));
  const source = all.find((l) => l.id === vehicle.location_id);
  const zone = source?.timezone ?? company.timezone ?? "UTC";
  const key = ["vehicle-moves", company.public_id, vehicle.id];
  const moves = useApi<{ moves: VehicleMove[] }>(key, `/c/${company.public_id}/vehicles/${vehicle.id}/moves`);
  const mayMove = can("vehicle.move") && vehicle.location_id !== null;
  const drivers = useApi<{ drivers: { employment_id: string; name: string }[] }>(
    ["drivers", company.public_id],
    `/c/${company.public_id}/drivers`,
    mayMove,
  );
  const [to, setTo] = useState("");
  const [driver, setDriver] = useState(company.employment?.id ?? "");
  const [start, setStart] = useState("");
  // Until edited, the end is «now» in the zone of the vehicle's location (known once loaded).
  const [endInput, setEnd] = useState<string | null>(null);
  const end = endInput ?? toLocalInput(new Date(), zone);
  const [note, setNote] = useState("");
  const [done, setDone] = useState<VehicleMove | null>(null);
  const move = useCommand<Record<string, string>, { move: VehicleMove }>(
    `/c/${company.public_id}/vehicles/${vehicle.id}/move`,
  );

  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await move.run({
      to_location_id: to,
      driver_employment_id: driver,
      actual_start: fromLocalInput(start, zone),
      actual_end: fromLocalInput(end, zone),
      note,
    });
    if (result) {
      setDone(result.move);
      setTo("");
      setNote("");
      setStart("");
      setEnd(null);
      for (const k of [key, ["vehicle", company.public_id, vehicle.id], ["vehicles", company.public_id], ["orders", company.public_id], ["locations", company.public_id]]) {
        await client.invalidateQueries({ queryKey: k });
      }
    }
  }

  const items = moves.data?.moves ?? [];
  return (
    <Card title={t("moves.title")}>
      {items.length === 0 ? <p className="muted">{t("moves.none")}</p> : null}
      {items.length ? (
        <table>
          <tbody>
            {items.map((m) => (
              <tr key={m.id}>
                <td>
                  {names.get(m.from_location_id) ?? "—"} → {names.get(m.to_location_id) ?? "—"}
                </td>
                <td>
                  {formatMoment(m.actual_start, zones.get(m.from_location_id) ?? zone, i18n.language)} —{" "}
                  {formatMoment(m.actual_end, zones.get(m.from_location_id) ?? zone, i18n.language)}
                </td>
                <td>{m.driver.name || "—"}</td>
                <td>
                  {can("order.view") ? (
                    <Link to={companyPath(company, `/orders/${m.order_id}`)}>{t("rentals.open_order")}</Link>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {mayMove ? (
        <form className="stack" onSubmit={submit} style={{ marginTop: 16 }}>
          <h3>{t("moves.record_title")}</h3>
          <p className="muted small">{t("moves.record_hint", { from: source?.name ?? "—" })}</p>
          <div className="row">
            <Field label={t("moves.to")}>
              <select value={to} onChange={(e) => setTo(e.target.value)}>
                <option value="">—</option>
                {all
                  .filter((l) => l.id !== vehicle.location_id && l.state !== "deactivated")
                  .map((l) => (
                    <option key={l.id} value={l.id}>
                      {l.name}
                    </option>
                  ))}
              </select>
            </Field>
            <Field label={t("moves.driver")}>
              <select value={driver} onChange={(e) => setDriver(e.target.value)}>
                <option value="">—</option>
                {(drivers.data?.drivers ?? []).map((d) => (
                  <option key={d.employment_id} value={d.employment_id}>
                    {d.name || d.employment_id}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <div className="row">
            <Field label={t("moves.actual_start")} hint={zone}>
              <input type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} />
            </Field>
            <Field label={t("moves.actual_end")} hint={zone}>
              <input type="datetime-local" value={end} onChange={(e) => setEnd(e.target.value)} />
            </Field>
            <Field label={t("payments.note")}>
              <input value={note} onChange={(e) => setNote(e.target.value)} />
            </Field>
          </div>
          <ErrorMessage error={move.error} />
          {done ? <div className="alert ok">{t("moves.done", { to: names.get(done.to_location_id) ?? "" })}</div> : null}
          <div className="actions">
            <button type="submit" disabled={move.pending || !to || !driver || !start || !end}>
              {t("moves.record")}
            </button>
          </div>
        </form>
      ) : null}
    </Card>
  );
}
