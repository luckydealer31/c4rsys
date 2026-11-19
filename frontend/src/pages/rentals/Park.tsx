import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import type { RentalSummary, Vehicle } from "../../api/types";
import { Card, Info, Loading } from "../../components/ui";
import { MomentField, openingTime } from "../../components/MomentField";
import { VehicleTypeIcon } from "../../components/VehicleTypeIcon";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { formatMoment, fromLocalInput, toLocalInput } from "../../time";
import { useLocations } from "../settings/LocationsPage";
import { RowTags, useTagsOf } from "../tags/Tags";
import { useRentals, useVehicles } from "../vehicles/VehiclesPage";

type Status = "free" | "rented" | "booked";
// What the list is filtered by: the occupancy, or the vehicles whose rental is restricted.
type Filter = Status | "limited";
const FILTERS: Filter[] = ["free", "rented", "booked", "limited"];
const FLEET = ["own", "managed"] as const;
// The ready periods the button goes round; «own» — a period typed by hand.
const MODES = ["now", "today", "tomorrow", "week", "month"] as const;
type Mode = (typeof MODES)[number] | "own";

/** The park of the rental (customer's decisions of 17.11.2026): the own and the managed vehicles
 * — those a rental and a booking are made of; the client ones are in the garage only. A vehicle
 * on a warehouse is a stock item and is not shown. The list is filtered by the occupancy — free,
 * in a rental, booked. A vehicle whose rental is restricted stays in the list, greyed: in place
 * of the buttons of its row stands the red mark «Restricted» with the reason under the pointer,
 * and it is not counted as free. A booking and a rental are begun from the row.
 *
 * The occupancy for a period (customer's decisions of 20.11.2026): with «from» and «to» given
 * the list answers for the whole period — a vehicle taken for a part of it is not free; a rental
 * without a planned end, or whose planned end has passed, holds the vehicle on; a restriction
 * counts when it overlaps the period. The booking begun from a row takes the period with it, and
 * the issue, which is of this moment, is not offered. Without a period the list is of now. */
export function Park() {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const vehicles = useVehicles();
  const locations = useLocations().data?.locations ?? [];
  const rentals = useRentals(can("rental.view")).data?.rentals ?? [];
  const [status, setStatus] = useState<Filter | "">("");
  // The period as typed, in the time of the company; empty — now.
  const zone = company.timezone ?? "UTC";
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const period = from || to;
  const begin = new Date(from ? fromLocalInput(from, zone) : Date.now()).getTime();
  const finish = Math.max(begin, new Date(to ? fromLocalInput(to, zone) : begin).getTime());
  /** Whether what lasts from `start` to `end` (null — no end) falls into the period. */
  const within = (start: string | null | undefined, end: string | null | undefined) =>
    new Date(start ?? 0).getTime() <= finish && (!end || new Date(end).getTime() >= begin);
  // One button goes round the ready periods (customer's decision of 20.11.2026): now, today and
  // tomorrow — a day from the beginning of the working day of that date, 7 days and a month
  // from this moment. A period typed by hand is «own»; the next press is «now».
  const [mode, setMode] = useState<Mode>("now");
  const opening = (date: string) => openingTime(locations, "rental", date);
  const ahead = (days: number) => toLocalInput(new Date(Date.now() + days * 86_400_000), zone);
  function next() {
    const turn = MODES[(MODES.indexOf(mode as (typeof MODES)[number]) + 1) % MODES.length] ?? "now";
    setMode(turn);
    if (turn === "now") {
      setFrom("");
      setTo("");
      return;
    }
    if (turn === "today" || turn === "tomorrow") {
      // A day from the beginning of the working day — of today or of tomorrow.
      const shift = turn === "today" ? 0 : 1;
      const day = ahead(shift).slice(0, 10);
      const time = opening(day);
      setFrom(`${day}T${time}`);
      setTo(`${ahead(shift + 1).slice(0, 10)}T${time}`);
      return;
    }
    // 7 days and a month — from this moment.
    const month = new Date();
    month.setMonth(month.getMonth() + 1);
    setFrom(ahead(0));
    setTo(turn === "week" ? ahead(7) : toLocalInput(month, zone));
  }
  const typed = (set: (value: string) => void) => (value: string) => {
    set(value);
    setMode(value || (set === setFrom ? to : from) ? "own" : "now");
  };
  const tagsOf = useTagsOf("vehicle");
  const [shown, setShown] = useState<Set<string>>(new Set(FLEET));
  // Both switches are on at first; the last one that is on stays on.
  const toggle = (key: (typeof FLEET)[number]) => {
    if (shown.has(key) && shown.size === 1) return;
    const next = new Set(shown);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setShown(next);
  };

  // What holds each vehicle: the rental it is in, and its bookings from the nearest one.
  const active = new Map<string, RentalSummary>();
  const booked = new Map<string, RentalSummary[]>();
  for (const rental of rentals) {
    // A rental going on holds the vehicle till its planned end, and on when that has passed.
    const held = !rental.planned_end || new Date(rental.planned_end).getTime() < Date.now() ? null : rental.planned_end;
    if (rental.state === "active" && (!period || within(rental.actual_start, held))) {
      active.set(rental.vehicle_card_id, rental);
    }
    if (rental.state === "booked" && rental.booking && (!period || within(rental.booking.planned_start, rental.planned_end))) {
      booked.set(rental.vehicle_card_id, [...(booked.get(rental.vehicle_card_id) ?? []), rental]);
    }
  }
  for (const list of booked.values()) {
    list.sort((a, b) => (a.booking?.planned_start ?? "").localeCompare(b.booking?.planned_start ?? ""));
  }
  // One state for a vehicle: a rental going on comes before a booking waiting.
  const statusOf = (v: Vehicle): Status => (active.has(v.id) ? "rented" : booked.has(v.id) ? "booked" : "free");
  /** Restricted: not admitted to the rental, or under a manual restriction forbidding the issue. */
  const forbids = (v: Vehicle) => {
    const r = v.restriction;
    if (!r || !r.forbid_rental_issue) return false;
    if (!period) return r.state === "active";
    return (r.state === "active" || r.state === "planned") && within(r.starts_at, r.ends_at);
  };
  const limited = (v: Vehicle) => !v.rental_readiness.ready || forbids(v);
  // A restricted vehicle is not free, though nothing holds it.
  const passes = (v: Vehicle) =>
    !status || (status === "limited" ? limited(v) : statusOf(v) === status && !(status === "free" && limited(v)));

  const fleet = (vehicles.data?.vehicles ?? []).filter((v) => FLEET.some((r) => r === v.relation) && v.storage === null);
  const list = fleet.filter((v) => shown.has(v.relation) && passes(v));
  const groups = [
    ...locations.map((l) => ({ id: l.id, name: l.name, zone: l.timezone, vehicles: list.filter((v) => v.location_id === l.id) })),
    {
      id: "none",
      name: t("vehicles.no_location"),
      zone: null,
      vehicles: list.filter((v) => !v.location_id || !locations.some((l) => l.id === v.location_id)),
    },
  ].filter((group) => group.vehicles.length > 0);

  /** What restricts the rental of the vehicle: what its admission lacks and the manual
   * restriction that forbids the issue now. */
  const reasons = (v: Vehicle, zone: string): string[] => {
    const r = v.restriction;
    return [
      ...(v.rental_readiness.ready
        ? []
        : [`${t("rentals.park_not_admitted")}: ${v.rental_readiness.missing.map((code) => t(`vehicles.missing_${code}`)).join(", ")}`]),
      ...(r && forbids(v)
        ? [
            `${r.custom_reason ? r.custom_reason.name : t(`vehicles.restriction_reason_${r.reason}`)}${
              r.ends_at ? ` · ${t("rentals.park_until", { when: formatMoment(r.ends_at, zone, i18n.language) })}` : ""
            }`,
          ]
        : []),
    ];
  };

  return (
    <>
      <div className="list-bar">
        <div className="segmented" role="group" aria-label={t("vehicles.occupancy")}>
          <button type="button" aria-pressed={status === ""} onClick={() => setStatus("")}>
            {t("rentals.park_all")}
          </button>
          {FILTERS.map((key) => (
            <button key={key} type="button" aria-pressed={status === key} onClick={() => setStatus(key)}>
              {t(`rentals.park_${key}`)}
            </button>
          ))}
        </div>
        {FLEET.map((key) => (
          <button key={key} type="button" className="toggle-chip" aria-pressed={shown.has(key)} onClick={() => toggle(key)}>
            {t(`vehicles.tab_${key}`)}
          </button>
        ))}
      </div>
      <div className="list-bar period-bar" role="group" aria-label={t("rentals.park_period")}>
        <button type="button" className="toggle-chip plain period-mode" aria-label={`${t("rentals.park_period")}: ${t(`rentals.park_mode_${mode}`)}`} onClick={next}>
          {t(`rentals.park_mode_${mode}`)}
        </button>
        <span className="period-part">
          {t("rentals.park_from")} <MomentField value={from} onChange={typed(setFrom)} opening={opening} label={t("rentals.park_from_label")} />
        </span>
        <span className="period-part">
          {t("rentals.park_to")} <MomentField value={to} onChange={typed(setTo)} opening={opening} label={t("rentals.park_to_label")} />
        </span>
        <Info text={t("rentals.park_period_hint")} />
      </div>
      {vehicles.isLoading ? <Loading /> : null}
      {vehicles.data && list.length === 0 ? (
        <Card>
          <p className="muted">{t(fleet.length ? "rentals.none_found" : "vehicles.none")}</p>
        </Card>
      ) : null}
      {groups.map((group) => {
        const zone = group.zone ?? company.timezone ?? "UTC";
        const when = (moment: string | null | undefined) => formatMoment(moment, zone, i18n.language);
        return (
          <Card key={group.id} title={group.name}>
            <div className="table-scroll">
              {/* The columns are of one width in every location, so they stand in one vertical. */}
              <table className="fleet-table">
                <colgroup>
                  <col />
                  <col className="col-state" />
                  <col className="col-odometer" />
                  <col className="col-actions" />
                </colgroup>
                <thead>
                  <tr>
                    <th>{t("vehicles.name")}</th>
                    <th>{t("vehicles.occupancy")}</th>
                    <th>{t("vehicles.odometer")}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {group.vehicles.map((vehicle) => {
                    const state = statusOf(vehicle);
                    const rental = active.get(vehicle.id);
                    const next = booked.get(vehicle.id)?.[0];
                    const restricted = limited(vehicle);
                    return (
                      <tr key={vehicle.id} className={restricted ? "limited" : undefined}>
                        <td>
                          <div className="vehicle-cell">
                            <VehicleTypeIcon
                              type={vehicle.characteristics.vehicle_type}
                              body={vehicle.characteristics.body_type}
                            />
                            <div>
                              <Link to={companyPath(company, `/vehicles/${vehicle.id}`)}>{vehicle.name}</Link>{" "}
                              {vehicle.relation === "managed" ? (
                                <span className="badge">{t("vehicles.relation_managed")}</span>
                              ) : null}{" "}
                              <RowTags tags={tagsOf(vehicle.id)} />
                              <div className="muted small">{vehicle.vehicle_id}</div>
                            </div>
                          </div>
                        </td>
                        <td data-label={t("vehicles.occupancy")}>
                          {restricted && state === "free" ? (
                            <span className="muted">—</span>
                          ) : (
                            <span className={`badge ${state === "free" ? "ok" : state === "rented" ? "warn" : ""}`}>
                              {t(`rentals.park_${state}`)}
                            </span>
                          )}
                          {rental ? (
                            <div className="small">
                              <Link to={companyPath(company, `/rentals/${rental.id}`)}>
                                {rental.planned_end
                                  ? t("rentals.park_until", { when: when(rental.planned_end) })
                                  : t("rentals.park_rental")}
                              </Link>
                            </div>
                          ) : null}
                          {next?.booking ? (
                            <div className="small">
                              <Link to={companyPath(company, `/rentals/${next.id}`)}>
                                {t(next.booking.direct ? "rentals.park_rental_from" : "rentals.park_booking_from", {
                                  number: next.booking.number,
                                  when: when(next.booking.planned_start),
                                })}
                              </Link>
                            </div>
                          ) : null}
                        </td>
                        <td data-label={t("vehicles.odometer")}>
                          {vehicle.odometer ? `${Number(vehicle.odometer.value)} ${vehicle.odometer.unit}` : "—"}
                        </td>
                        <td className="actions-cell">
                          <div className="row-actions">
                            {/* In place of the buttons: the mark, the reason under the pointer. */}
                            {restricted ? (
                              <span
                                className="badge danger tip-left"
                                tabIndex={0}
                                role="note"
                                aria-label={`${t("rentals.park_limited")}: ${reasons(vehicle, zone).join("; ")}`}
                                data-tip={reasons(vehicle, zone).join("; ")}
                              >
                                {t("rentals.park_limited")}
                              </span>
                            ) : null}
                            {!restricted && can("rental.book") ? (
                              <Link
                                className="button secondary small-button"
                                to={companyPath(
                                  company,
                                  `/rentals/book?vehicle=${vehicle.id}${from ? `&from=${from}` : ""}${to ? `&to=${to}` : ""}`,
                                )}
                              >
                                {t("rentals.park_book")}
                              </Link>
                            ) : null}
                            {!restricted && !period && state !== "rented" && can("rental.issue") ? (
                              <Link
                                className="button secondary small-button"
                                to={companyPath(company, `/rentals/new?vehicle=${vehicle.id}`)}
                              >
                                {t("rentals.park_issue")}
                              </Link>
                            ) : null}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Card>
        );
      })}
    </>
  );
}
