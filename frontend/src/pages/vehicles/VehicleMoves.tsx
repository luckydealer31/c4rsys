import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { useApi } from "../../api/queries";
import type { Delivery, Vehicle, VehicleMove } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { formatMoment, fromLocalInput, toLocalInput } from "../../time";
import { useLocations } from "../settings/LocationsPage";

/** Moves of the vehicle and recording a move that already happened, in one action: destination,
 * driver, actual start and end (§ 8.5.2, § 15.1.3–15.1.5, § 23.6.2; decision 23.10.2026). It is
 * a trip completed at once (decision Д-1); an internal order of the company is created for it.
 * Moves of stage 1 stay in the list as they were. */
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
  const trips = useApi<{ deliveries: Delivery[] }>(
    ["deliveries", company.public_id, "vehicle", vehicle.id],
    `/c/${company.public_id}/deliveries?vehicle_card_id=${vehicle.id}`,
    can("delivery.view") || can("vehicle.move"),
  );
  const mayMove = can("vehicle.move") && vehicle.location_id !== null;
  const drivers = useApi<{ drivers: { id: string; name: string }[] }>(
    ["delivery-drivers", company.public_id],
    `/c/${company.public_id}/deliveries/drivers`,
    mayMove,
  );
  const [to, setTo] = useState("");
  const [driver, setDriver] = useState(company.employment?.id ?? "");
  const [start, setStart] = useState("");
  // Until edited, the end is «now» in the zone of the vehicle's location (known once loaded).
  const [endInput, setEnd] = useState<string | null>(null);
  const end = endInput ?? toLocalInput(new Date(), zone);
  const [note, setNote] = useState("");
  // § 8.8.7, § 23.5.6: a restricted vehicle goes to a service only as an exception with a reason.
  const [exception, setException] = useState("");
  const restricted = vehicle.restriction?.state === "active" && vehicle.restriction.forbid_moves;
  const [done, setDone] = useState<Delivery | null>(null);
  const move = useCommand<Record<string, unknown>, { delivery: Delivery }>(`/c/${company.public_id}/deliveries/record`);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await move.run({
      vehicle_card_id: vehicle.id,
      to: { kind: "location", location_id: to },
      driver_id: driver,
      actual_start: fromLocalInput(start, zone),
      actual_end: fromLocalInput(end, zone),
      result: "accepted_in_location",
      comment: note,
      ...(restricted && exception.trim() ? { restriction_exception_reason: exception } : {}),
    });
    if (result) {
      setDone(result.delivery);
      setTo("");
      setNote("");
      setStart("");
      setEnd(null);
      for (const k of [key, ["deliveries", company.public_id], ["vehicle", company.public_id, vehicle.id], ["vehicles", company.public_id], ["orders", company.public_id], ["locations", company.public_id]]) {
        await client.invalidateQueries({ queryKey: k });
      }
    }
  }

  const items = moves.data?.moves ?? [];
  const trip = trips.data?.deliveries ?? [];
  return (
    <Card title={t("moves.title")}>
      {items.length === 0 && trip.length === 0 ? <p className="muted">{t("moves.none")}</p> : null}
      {trip.length ? (
        <table>
          <tbody>
            {trip.map((d) => (
              <tr key={d.id}>
                <td>
                  <Link to={companyPath(company, `/trips/${d.id}`)}>№ {d.number}</Link>
                </td>
                <td>
                  {names.get(d.from.location) ?? "—"} → {d.to.kind === "other" ? d.to.address || d.to.name : (names.get(d.to.location) ?? "—")}
                </td>
                <td>{t(`trips.state_${d.state}`)}</td>
                <td>{d.driver_name ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
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
                  <option key={d.id} value={d.id}>
                    {d.name || d.id}
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
          {restricted ? (
            <div className="row">
              <div className="alert warn" style={{ flexBasis: "100%" }}>
                {t("moves.restricted_hint")}
              </div>
              {can("vehicle.move_restricted_to_service") ? (
                <Field label={t("moves.restriction_exception")} hint={t("moves.restriction_exception_hint")}>
                  <input value={exception} onChange={(e) => setException(e.target.value)} />
                </Field>
              ) : null}
            </div>
          ) : null}
          <ErrorMessage error={move.error} />
          {done ? <div className="alert ok">{t("moves.done", { to: names.get(done.to.location) ?? "" })}</div> : null}
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
