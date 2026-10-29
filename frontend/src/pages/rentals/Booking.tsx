import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router";

import { api, ApiError } from "../../api/client";
import { useApi } from "../../api/queries";
import type { BookingConflict, Client, Rental } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { formatMoment, fromLocalInput, toLocalInput } from "../../time";
import { clientName } from "../clients/ClientsPage";
import { useLocations } from "../settings/LocationsPage";
import { useVehicles } from "../vehicles/VehiclesPage";
import { ClientFinder } from "./IssuePage";
import {
  EMPTY_PLACE,
  GrantException,
  PlaceFields,
  placeBody,
  placeOf,
  placeText,
  type PlaceValue,
} from "./IssueRules";

type VehicleTariff = { id: string; name: string; currency: string };
type RentalResponse = { rental: Rental };

export function useVehicleTariffs(vehicleId: string) {
  const company = useCompany();
  return useApi<{ tariffs: VehicleTariff[] }>(
    ["vehicle-tariffs", company.public_id, vehicleId],
    `/c/${company.public_id}/vehicles/${vehicleId}/tariffs`,
    Boolean(vehicleId),
  );
}

/** Operations of the vehicle overlapping the period, before the booking is saved (§ 14.6). */
function Availability({
  vehicleId,
  start,
  end,
  exclude,
}: {
  vehicleId: string;
  start: string | null;
  end: string | null;
  exclude?: string;
}) {
  const { t } = useTranslation();
  const company = useCompany();
  const [result, setResult] = useState<{ free: boolean; conflicts: BookingConflict[] } | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  async function check() {
    setError(null);
    setResult(null);
    const query = new URLSearchParams({ vehicle_card_id: vehicleId, start: start ?? "", end: end ?? "" });
    if (exclude) query.set("exclude", exclude);
    try {
      setResult(await api(`/c/${company.public_id}/bookings/availability?${query.toString()}`));
    } catch (caught) {
      setError(caught as ApiError);
    }
  }

  return (
    <>
      <div className="actions" style={{ marginTop: 0 }}>
        <button type="button" className="secondary" disabled={!vehicleId || !start || !end} onClick={() => void check()}>
          {t("bookings.check")}
        </button>
      </div>
      <ErrorMessage error={error} />
      {result?.free ? <div className="alert">{t("bookings.free")}</div> : null}
      {result && result.conflicts.length ? (
        <div className="alert warn">
          {result.free ? t("bookings.deliveries_warning") : t("bookings.busy")}{" "}
          {result.conflicts.map((c) => (
            <Link key={c.id} to={companyPath(company, conflictPath(c))} style={{ marginRight: 8 }}>
              {conflictLabel(c, t)}
            </Link>
          ))}
        </div>
      ) : null}
    </>
  );
}

function conflictPath(c: BookingConflict): string {
  if (c.kind === "service") return `/service/operations/${c.id}`;
  if (c.kind === "delivery") return `/trips/${c.id}`;
  return `/rentals/${c.id}`;
}

function conflictLabel(c: BookingConflict, t: ReturnType<typeof useTranslation>["t"]): string {
  if (c.kind === "booking") return t("bookings.number", { number: c.number });
  if (c.kind === "service") return t("service.operation_title", { number: c.number });
  if (c.kind === "delivery") return t("trips.title", { number: c.number });
  return t(`rentals.state_${c.state}`);
}

/** New booking (§ 14.1): a concrete vehicle (filtered by body type) and the planned period; the
 * client, tariff, location, contact and comment may come later (§ 14.1.8–14.1.9). */
export function BookingPage() {
  const { t } = useTranslation();
  const company = useCompany();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const vehicles = useVehicles();
  const locations = useLocations();
  const [body, setBody] = useState("");
  const [vehicleId, setVehicleId] = useState("");
  const [tariffId, setTariffId] = useState("");
  const [locationId, setLocationId] = useState("");
  const [chosen, setChosen] = useState<Client | null>(null);
  const location = (locations.data?.locations ?? []).find((l) => l.id === locationId);
  const zone = location?.timezone ?? company.timezone ?? "UTC";
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [contact, setContact] = useState("");
  const [comment, setComment] = useState("");
  const [place, setPlace] = useState<PlaceValue>(EMPTY_PLACE);
  const tariffs = useVehicleTariffs(vehicleId);
  const create =useCommand<Record<string, unknown>, RentalResponse>(`/c/${company.public_id}/bookings`);
  const rentable = (vehicles.data?.vehicles ?? []).filter((v) => v.contexts.includes("rental"));
  const bodies = [...new Set(rentable.map((v) => v.characteristics.body_type).filter(Boolean))].sort();
  const startAt = start ? fromLocalInput(start, zone) : null;
  const endAt = end ? fromLocalInput(end, zone) : null;

  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await create.run({
      vehicle_card_id: vehicleId,
      planned_start: startAt,
      planned_end: endAt,
      client_id: chosen?.id ?? "",
      tariff_id: tariffId,
      issue_location_id: locationId,
      ...(locationId ? placeBody(place) : {}),
      contact,
      comment,
    });
    if (result) {
      await queryClient.invalidateQueries({ queryKey: ["rentals", company.public_id] });
      navigate(companyPath(company, `/rentals/${result.rental.id}`));
    }
  }

  return (
    <div className="page">
      <h1>{t("bookings.new")}</h1>
      <form onSubmit={submit}>
        <Card title={t("bookings.vehicle_and_period")}>
          <div className="row">
            <Field label={t("vehicles.body_type")}>
              <select value={body} onChange={(e) => setBody(e.target.value)}>
                <option value="">{t("bookings.any_body")}</option>
                {bodies.map((b) => (
                  <option key={b} value={b}>
                    {t(`vehicles.body.${b}`, { defaultValue: b })}
                  </option>
                ))}
              </select>
            </Field>
            <Field label={t("rentals.vehicle")}>
              <select
                value={vehicleId}
                onChange={(e) => {
                  setVehicleId(e.target.value);
                  setTariffId("");
                }}
              >
                <option value="">—</option>
                {rentable
                  .filter((v) => !body || v.characteristics.body_type === body)
                  .map((v) => (
                    <option key={v.id} value={v.id}>
                      {v.name} · {v.vehicle_id}
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
          <Availability vehicleId={vehicleId} start={startAt} end={endAt} />
        </Card>
        <Card title={t("bookings.details")}>
          <p className="muted small">{t("bookings.details_later")}</p>
          <div className="row">
            <Field label={t("rentals.tariff")}>
              <select value={tariffId} onChange={(e) => setTariffId(e.target.value)}>
                <option value="">—</option>
                {(tariffs.data?.tariffs ?? []).map((tariff) => (
                  <option key={tariff.id} value={tariff.id}>
                    {tariff.name} · {tariff.currency}
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
          {locationId ? <PlaceFields value={place} onChange={setPlace} /> : null}
          <div className="row">
            <Field label={t("bookings.contact")}>
              <input value={contact} onChange={(e) => setContact(e.target.value)} />
            </Field>
            <Field label={t("bookings.comment")}>
              <input value={comment} onChange={(e) => setComment(e.target.value)} />
            </Field>
          </div>
          <h3>{t("rentals.client")}</h3>
          {chosen ? (
            <p>
              <strong>{clientName(chosen)}</strong>{" "}
              <button type="button" className="secondary" onClick={() => setChosen(null)}>
                {t("rentals.change_client")}
              </button>
            </p>
          ) : (
            <ClientFinder onPick={setChosen} />
          )}
        </Card>
        <ErrorMessage error={create.error} />
        <div className="actions">
          <button type="submit" disabled={create.pending || !vehicleId || !start || !end}>
            {t("bookings.create")}
          </button>
        </div>
      </form>
    </div>
  );
}

type Panel = "" | "edit" | "vehicle" | "cancel" | "auto" | "deposit" | "exception";

/** The booking block of the rental card: number, «client not specified», a late return of the
 * vehicle (§ 14.6.2), a different current location of the vehicle (§ 14.2.2) and the actions
 * before the issue (§ 14.1.6, § 14.7.3, § 14.8.1). */
export function BookingCard({ rental, onDone }: { rental: Rental; onDone: (next?: Rental) => Promise<void> }) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const locations = useLocations();
  const [panel, setPanel] = useState<Panel>("");
  const booking = rental.booking;
  if (!booking) return null;
  const zone = rental.calc_timezone;
  const when = (value: string | null | undefined) => formatMoment(value, zone, i18n.language);
  const place = new Map((locations.data?.locations ?? []).map((l) => [l.id, l.name]));
  const booked = rental.state === "booked";
  const elsewhere =
    booked &&
    rental.issue_location_id !== null &&
    booking.vehicle_location_id !== null &&
    booking.vehicle_location_id !== rental.issue_location_id;
  const done = async (next?: Rental) => {
    setPanel("");
    await onDone(next);
  };
  const panels: Panel[] = [];
  if (booked && can("rental.book")) panels.push("edit", "vehicle", "auto", "cancel");
  if (booked && can("finance.deposit.manage")) panels.push("deposit");
  if (booked && can("rental.exception.authorize")) panels.push("exception");
  const granted = booked && rental.issue_exception?.state === "granted" ? rental.issue_exception : null;

  return (
    <div className="stack">
      {booked && booking.client_missing ? <div className="alert warn">{t("bookings.client_missing")}</div> : null}
      {booking.delay_conflict ? (
        <div className="alert warn">
          {t(booking.delay_conflict.overdue ? "bookings.delay_overdue" : "bookings.delay_planned", {
            end: when(booking.delay_conflict.planned_end),
          })}{" "}
          <Link to={companyPath(company, `/rentals/${booking.delay_conflict.rental}`)}>{t("bookings.open_rental")}</Link>
        </div>
      ) : null}
      {elsewhere ? (
        <div className="alert warn">
          {t("bookings.vehicle_elsewhere", { place: place.get(booking.vehicle_location_id ?? "") ?? "—" })}
        </div>
      ) : null}
      <dl className="facts">
        <dt>{t("bookings.number_label")}</dt>
        <dd>{booking.number}</dd>
        <dt>{t("bookings.planned_start")}</dt>
        <dd>{when(booking.planned_start)}</dd>
        <dt>{t("rentals.planned_end")}</dt>
        <dd>{when(rental.planned_end)}</dd>
        <dt>{t("bookings.contact")}</dt>
        <dd>{booking.contact || "—"}</dd>
        <dt>{t("bookings.comment")}</dt>
        <dd>{booking.comment || "—"}</dd>
        <dt>{t("bookings.auto_cancel_at")}</dt>
        <dd>{booking.auto_cancel_at ? when(booking.auto_cancel_at) : t("bookings.auto_cancel_off")}</dd>
        {booking.deposit_override ? (
          <>
            <dt>{t("bookings.deposit_changed")}</dt>
            <dd>
              {booking.deposit_override.required
                ? `${booking.deposit_override.amount ?? "—"} ${rental.deposit?.currency ?? ""}`
                : t("bookings.deposit_not_required")}{" "}
              · {booking.deposit_override.reason}
            </dd>
          </>
        ) : null}
        {rental.issue_place_kind === "other" ? (
          <>
            <dt>{t("rentals.place_other")}</dt>
            <dd>{placeText(rental)}</dd>
          </>
        ) : null}
        {granted ? (
          <>
            <dt>{t("issue_rules.state_granted")}</dt>
            <dd>
              {granted.requirements.map((code) => t(`issue_rules.code_${code}`)).join(", ")} · {granted.reason}
            </dd>
          </>
        ) : null}
        {rental.state === "cancelled" ? (
          <>
            <dt>{t("bookings.cancelled_at")}</dt>
            <dd>
              {when(booking.cancelled_at)} ·{" "}
              {booking.cancel_reason === "no_show" ? t("bookings.reason_no_show") : booking.cancel_reason}
            </dd>
          </>
        ) : null}
      </dl>
      {booked ? (
        <div className="actions">
          {can("rental.issue") ? (
            <Link className="button" to={companyPath(company, `/rentals/new?booking=${rental.id}`)}>
              {t("bookings.issue")}
            </Link>
          ) : null}
          {panels.map((p) => (
            <button key={p} type="button" className="secondary" onClick={() => setPanel(panel === p ? "" : p)}>
              {t(`bookings.action_${p}`)}
            </button>
          ))}
        </div>
      ) : null}
      {panel === "edit" ? <EditBooking rental={rental} onDone={done} /> : null}
      {panel === "vehicle" ? <ReplaceVehicle rental={rental} onDone={done} /> : null}
      {panel === "auto" ? <AutoCancel rental={rental} onDone={done} /> : null}
      {panel === "cancel" ? <CancelBooking rental={rental} onDone={done} /> : null}
      {panel === "deposit" ? <BookingDeposit rental={rental} onDone={done} /> : null}
      {panel === "exception" ? <GrantException rental={rental} onDone={done} /> : null}
    </div>
  );
}

type FormProps = { rental: Rental; onDone: (next?: Rental) => Promise<void> };

function bookingPath(cmp: string, rental: Rental, action = "") {
  return `/c/${cmp}/bookings/${rental.id}${action ? `/${action}` : ""}`;
}

/** Period, client, tariff, location, contact and comment; only changed fields are sent. A new
 * start moves the auto-cancel moment, the end stays as entered. */
function EditBooking({ rental, onDone }: FormProps) {
  const { t } = useTranslation();
  const company = useCompany();
  const locations = useLocations();
  const booking = rental.booking;
  const zone = rental.calc_timezone;
  const [start, setStart] = useState(() => (booking ? toLocalInput(new Date(booking.planned_start), zone) : ""));
  const [end, setEnd] = useState(() => (rental.planned_end ? toLocalInput(new Date(rental.planned_end), zone) : ""));
  const [tariffId, setTariffId] = useState(rental.tariff_id ?? "");
  const [locationId, setLocationId] = useState(rental.issue_location_id ?? "");
  const [contact, setContact] = useState(booking?.contact ?? "");
  const [comment, setComment] = useState(booking?.comment ?? "");
  const [chosen, setChosen] = useState<Client | null>(null);
  const [place, setPlace] = useState<PlaceValue>(() => placeOf(rental));
  const tariffs = useVehicleTariffs(rental.vehicle_card_id);
  const save = useCommand<Record<string, unknown>, RentalResponse>(bookingPath(company.public_id, rental), "PATCH");
  if (!booking) return null;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!booking) return;
    const changes: Record<string, unknown> = { expected_version: rental.version };
    const startAt = start ? fromLocalInput(start, zone) : null;
    const endAt = end ? fromLocalInput(end, zone) : null;
    if (startAt && new Date(startAt).getTime() !== new Date(booking.planned_start).getTime()) changes.planned_start = startAt;
    if (endAt && (!rental.planned_end || new Date(endAt).getTime() !== new Date(rental.planned_end).getTime()))
      changes.planned_end = endAt;
    if (tariffId && tariffId !== rental.tariff_id) changes.tariff_id = tariffId;
    const placeChanged = JSON.stringify(placeBody(place)) !== JSON.stringify(placeBody(placeOf(rental)));
    if (locationId && locationId !== rental.issue_location_id) changes.issue_location_id = locationId;
    if (locationId && (placeChanged || !rental.issue_place_kind)) Object.assign(changes, placeBody(place));
    if (chosen) changes.client_id = chosen.id;
    if (contact !== booking.contact) changes.contact = contact;
    if (comment !== booking.comment) changes.comment = comment;
    const result = await save.run(changes);
    if (result) await onDone(result.rental);
  }

  return (
    <form onSubmit={submit}>
      <div className="row">
        <Field label={t("bookings.planned_start")} hint={zone}>
          <input type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} />
        </Field>
        <Field label={t("rentals.planned_end")} hint={zone}>
          <input type="datetime-local" value={end} onChange={(e) => setEnd(e.target.value)} />
        </Field>
      </div>
      <Availability
        vehicleId={rental.vehicle_card_id}
        start={start ? fromLocalInput(start, zone) : null}
        end={end ? fromLocalInput(end, zone) : null}
        exclude={rental.id}
      />
      <div className="row">
        <Field label={t("rentals.tariff")}>
          <select value={tariffId} onChange={(e) => setTariffId(e.target.value)}>
            <option value="">—</option>
            {(tariffs.data?.tariffs ?? []).map((tariff) => (
              <option key={tariff.id} value={tariff.id}>
                {tariff.name} · {tariff.currency}
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
      {locationId ? <PlaceFields value={place} onChange={setPlace} /> : null}
      <div className="row">
        <Field label={t("bookings.contact")}>
          <input value={contact} onChange={(e) => setContact(e.target.value)} />
        </Field>
        <Field label={t("bookings.comment")}>
          <input value={comment} onChange={(e) => setComment(e.target.value)} />
        </Field>
      </div>
      <h3>{rental.client_id ? t("rentals.change_client") : t("rentals.client")}</h3>
      {chosen ? (
        <p>
          <strong>{clientName(chosen)}</strong>{" "}
          <button type="button" className="secondary" onClick={() => setChosen(null)}>
            {t("rentals.change_client")}
          </button>
        </p>
      ) : (
        <ClientFinder onPick={setChosen} />
      )}
      <ErrorMessage error={save.error} />
      <div className="actions">
        <button type="submit" disabled={save.pending}>
          {t("common.save")}
        </button>
      </div>
    </form>
  );
}

/** Another vehicle for the booking (§ 14.1.6); the tariff stays when the new vehicle has it. */
function ReplaceVehicle({ rental, onDone }: FormProps) {
  const { t } = useTranslation();
  const company = useCompany();
  const vehicles = useVehicles();
  const [vehicleId, setVehicleId] = useState("");
  const [tariffId, setTariffId] = useState("");
  const tariffs = useVehicleTariffs(vehicleId);
  const replace = useCommand<Record<string, unknown>, RentalResponse>(bookingPath(company.public_id, rental, "vehicle"));

  async function submit(event: FormEvent) {
    event.preventDefault();
    const body: Record<string, unknown> = { vehicle_card_id: vehicleId, expected_version: rental.version };
    if (tariffId) body.tariff_id = tariffId;
    const result = await replace.run(body);
    if (result) await onDone(result.rental);
  }

  return (
    <form onSubmit={submit}>
      <div className="row">
        <Field label={t("bookings.new_vehicle")}>
          <select value={vehicleId} onChange={(e) => setVehicleId(e.target.value)}>
            <option value="">—</option>
            {(vehicles.data?.vehicles ?? [])
              .filter((v) => v.contexts.includes("rental") && v.id !== rental.vehicle_card_id)
              .map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name} · {v.vehicle_id}
                </option>
              ))}
          </select>
        </Field>
        <Field label={t("rentals.tariff")} hint={t("bookings.tariff_kept")}>
          <select value={tariffId} onChange={(e) => setTariffId(e.target.value)}>
            <option value="">—</option>
            {(tariffs.data?.tariffs ?? []).map((tariff) => (
              <option key={tariff.id} value={tariff.id}>
                {tariff.name} · {tariff.currency}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <ErrorMessage error={replace.error} />
      <div className="actions">
        <button type="submit" disabled={replace.pending || !vehicleId}>
          {t("bookings.action_vehicle")}
        </button>
      </div>
    </form>
  );
}

function CancelBooking({ rental, onDone }: FormProps) {
  const { t } = useTranslation();
  const company = useCompany();
  const [reason, setReason] = useState("");
  const cancel = useCommand<Record<string, unknown>, RentalResponse>(bookingPath(company.public_id, rental, "cancel"));

  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await cancel.run({ reason, expected_version: rental.version });
    if (result) await onDone(result.rental);
  }

  return (
    <form onSubmit={submit}>
      <Field label={t("bookings.cancel_reason")}>
        <input value={reason} onChange={(e) => setReason(e.target.value)} />
      </Field>
      <ErrorMessage error={cancel.error} />
      <div className="actions">
        <button type="submit" className="danger" disabled={cancel.pending || !reason.trim()}>
          {t("bookings.cancel")}
        </button>
      </div>
    </form>
  );
}

/** Another auto-cancel moment for this booking, or none (§ 14.8.1). */
function AutoCancel({ rental, onDone }: FormProps) {
  const { t } = useTranslation();
  const company = useCompany();
  const zone = rental.calc_timezone;
  const current = rental.booking?.auto_cancel_at;
  const [at, setAt] = useState(() => (current ? toLocalInput(new Date(current), zone) : ""));
  const save = useCommand<Record<string, unknown>, RentalResponse>(bookingPath(company.public_id, rental, "auto-cancel"));

  async function run(value: string | null) {
    const result = await save.run({ auto_cancel_at: value, expected_version: rental.version });
    if (result) await onDone(result.rental);
  }

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void run(at ? fromLocalInput(at, zone) : null);
      }}
    >
      <Field label={t("bookings.auto_cancel_at")} hint={zone}>
        <input type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} />
      </Field>
      <ErrorMessage error={save.error} />
      <div className="actions">
        <button type="submit" disabled={save.pending || !at}>
          {t("common.save")}
        </button>
        <button type="button" className="secondary" disabled={save.pending || !current} onClick={() => void run(null)}>
          {t("bookings.auto_cancel_remove")}
        </button>
      </div>
    </form>
  );
}

/** The deposit of the booking changed or cancelled with a reason before the issue (§ 14.7.3). */
function BookingDeposit({ rental, onDone }: FormProps) {
  const { t } = useTranslation();
  const company = useCompany();
  const [required, setRequired] = useState(rental.deposit ? rental.deposit.required !== "0.00" : true);
  const [amount, setAmount] = useState(rental.deposit?.required ?? "");
  const [reason, setReason] = useState("");
  const save = useCommand<Record<string, unknown>, RentalResponse>(bookingPath(company.public_id, rental, "deposit"));

  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await save.run({
      required,
      amount: required ? amount : null,
      reason,
      expected_version: rental.version,
    });
    if (result) await onDone(result.rental);
  }

  return (
    <form onSubmit={submit}>
      <label className="small">
        <input type="checkbox" checked={required} onChange={() => setRequired(!required)} /> {t("bookings.deposit_required")}
      </label>
      <div className="row">
        {required ? (
          <Field label={t("bookings.deposit_amount", { currency: rental.deposit?.currency ?? "" })}>
            <input value={amount} inputMode="decimal" onChange={(e) => setAmount(e.target.value)} />
          </Field>
        ) : null}
        <Field label={t("bookings.deposit_reason")}>
          <input value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
      </div>
      <ErrorMessage error={save.error} />
      <div className="actions">
        <button type="submit" disabled={save.pending || !reason.trim() || (required && !amount)}>
          {t("common.save")}
        </button>
      </div>
    </form>
  );
}
