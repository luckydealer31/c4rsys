import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router";

import { api, ApiError } from "../../api/client";
import { useApi } from "../../api/queries";
import type {
  Calculation,
  Client,
  IssueException,
  IssueRequirement,
  TariffRange,
  Rental,
  Warning,
} from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, IconButton, Loading, Modal } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { fromLocalInput, toLocalInput } from "../../time";
import { ClientForm, clientName } from "../clients/ClientsPage";
import { useLocations } from "../settings/LocationsPage";
import { unitShort } from "../tariffs/TariffsPage";
import { useRentals, useVehicles } from "../vehicles/VehiclesPage";
import {
  EMPTY_PLACE,
  ExceptionChoice,
  PlaceFields,
  placeBody,
  placeOf,
  type ExceptionValue,
  type PlaceValue,
} from "./IssueRules";
import { usePeriodCount } from "./period";

type VehicleTariff = { id: string; name: string; currency: string; unit: string; ranges: TariffRange[] };

const UNMET_WARNING: Record<IssueRequirement["code"], string> = {
  payment: "PAYMENT_INCOMPLETE",
  debt: "CLIENT_HAS_DEBT",
  documents: "DOCUMENTS_MISSING",
};

/** The preliminary calculation with the policies of the issue (§ 13.6.6, § 14.3). */
type Quote = {
  calculation: Calculation;
  warnings: Warning[];
  unmet: IssueRequirement[];
  inspection_required: boolean;
  can_authorize: boolean;
  granted: IssueException | null;
};

/** Issue without a booking (§ 14.2–14.4): vehicle, client (search or create on the spot), tariff
 * of the vehicle, issue location and «in location / other», actual time (now by default),
 * odometer, fuel, planned end. The preliminary calculation and warnings come before confirming
 * (§ 13.6.6). */
export function IssuePage() {
  const company = useCompany();
  const [params] = useSearchParams();
  // § 14.4.1: the issue by a booking starts from what the booking already has.
  const bookingId = params.get("booking") ?? "";
  const booking = useApi<{ rental: Rental }>(
    ["rental", company.public_id, bookingId],
    `/c/${company.public_id}/rentals/${bookingId}`,
    Boolean(bookingId),
  );
  // A booking made without a client may have got one in its order since: the issue starts
  // from that client instead of asking again (fix of 16.11.2026).
  const orderId = booking.data && !booking.data.rental.client_id ? booking.data.rental.order_id : "";
  const order = useApi<{ order: { client_id: string | null } }>(
    ["order", company.public_id, orderId],
    `/c/${company.public_id}/orders/${orderId}`,
    Boolean(orderId),
  );
  const clientId = booking.data?.rental.client_id ?? order.data?.order.client_id ?? "";
  const bookedClient = useApi<{ client: Client }>(
    ["client", company.public_id, clientId],
    `/c/${company.public_id}/clients/${clientId}`,
    Boolean(clientId),
  );
  if (bookingId && !booking.data) {
    return <div className="page">{booking.error ? <ErrorMessage error={booking.error as ApiError} /> : <Loading />}</div>;
  }
  if ((orderId && order.isLoading) || (clientId && bookedClient.isLoading)) {
    return (
      <div className="page">
        <Loading />
      </div>
    );
  }
  return (
    <IssueForm
      vehicle={params.get("vehicle") ?? ""}
      booking={booking.data?.rental ?? null}
      bookedClient={bookedClient.data?.client ?? null}
    />
  );
}

function IssueForm({
  vehicle: preset,
  booking,
  bookedClient,
}: {
  vehicle: string;
  booking: Rental | null;
  bookedClient: Client | null;
}) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const navigate = useNavigate();
  const route = useLocation();
  const client = useQueryClient();
  const vehicles = useVehicles();
  const locations = useLocations();
  const rentals = useRentals(can("rental.view"));
  const busy = new Set((rentals.data?.rentals ?? []).filter((r) => r.state === "active").map((r) => r.vehicle_card_id));
  const [vehicleId, setVehicleId] = useState(booking?.vehicle_card_id ?? preset);
  const vehicle = vehicles.data?.vehicles.find((v) => v.id === vehicleId);
  const [locationId, setLocationId] = useState(booking?.issue_location_id ?? "");
  const location = (locations.data?.locations ?? []).find((l) => l.id === (locationId || vehicle?.location_id));
  const zone = location?.timezone ?? company.timezone ?? "UTC";
  const tariffs = useApi<{ tariffs: VehicleTariff[] }>(
    ["vehicle-tariffs", company.public_id, vehicleId],
    `/c/${company.public_id}/vehicles/${vehicleId}/tariffs`,
    Boolean(vehicleId),
  );
  const [chosen, setChosen] = useState<Client | null>(bookedClient);
  const [newClient, setNewClient] = useState(false);
  const [tariffId, setTariffId] = useState(booking?.tariff_id ?? "");
  const [place, setPlace] = useState<PlaceValue>(() => (booking ? placeOf(booking) : EMPTY_PLACE));
  const [exception, setException] = useState<ExceptionValue>({ mode: "", reason: "" });
  const [start, setStart] = useState(() => toLocalInput(new Date(), zone));
  const [plannedEnd, setPlannedEnd] = useState(() =>
    booking?.planned_end ? toLocalInput(new Date(booking.planned_end), zone) : "",
  );
  // The inspection at the issue made for this rental before the issue, if any.
  const ahead = Boolean(booking?.booking?.direct);
  const made = booking?.inspections.issue ?? null;
  const waiting = made && made.state !== "saved" ? made : null;
  const inspected = made && made.state === "saved" && made.odometer !== null && made.fuel !== null ? made : null;
  // An ordinary booking is never issued here (decision of 16.11.2026): it is turned into a
  // rental — with its inspection at the issue, or «without an inspection» — and the issue is
  // made from that rental.
  const plain = Boolean(booking) && !ahead;
  const [withoutInspection, setWithoutInspection] = useState(false);
  const converting = plain;
  const [plannedStart, setPlannedStart] = useState(() =>
    booking?.booking ? toLocalInput(new Date(booking.booking.planned_start), zone) : "",
  );
  // The start shown: the planned one while the booking only becomes a rental, else the actual.
  const from = converting ? plannedStart : start;
  const toRental = useCommand<Record<string, unknown>, { rental: Rental }>(
    `/c/${company.public_id}/bookings/${booking?.id ?? ""}/to-rental`,
  );
  const [units, setUnits] = useState("");
  const tariff = (tariffs.data?.tariffs ?? []).find((item) => item.id === tariffId);
  const count = usePeriodCount();
  async function recount(by: "units" | "end", next: { tariffId?: string; start?: string; end?: string; units?: string }) {
    const values = { tariffId, start: from, end: plannedEnd, units, ...next };
    const answer = await count(by, { vehicleId, locationId: location?.id ?? "", zone, ...values });
    if (!answer) return;
    if (by === "units") setPlannedEnd(toLocalInput(new Date(answer.end), zone));
    else setUnits(answer.units ? String(answer.units) : "");
  }
  const [odometer, setOdometer] = useState("");
  const [fuel, setFuel] = useState("");
  const [quote, setQuote] = useState<Quote | null>(null);
  const [quoteError, setQuoteError] = useState<ApiError | null>(null);
  const issue = useCommand<Record<string, unknown>, { rental: Rental; warnings: Warning[] }>(
    `/c/${company.public_id}/rentals/issue`,
  );
  // Strict requirements left unmet: from the preliminary check or from the refused issue.
  const refused = issue.error?.code === "ISSUE_REQUIREMENTS_UNMET" ? issue.error.params : null;
  const unmet = (refused?.requirements as IssueRequirement[] | undefined) ?? quote?.unmet ?? [];
  const canAuthorize = Boolean(refused?.can_authorize ?? quote?.can_authorize);

  async function calculate() {
    setQuoteError(null);
    try {
      setQuote(
        await api(`/c/${company.public_id}/rentals/quote`, {
          method: "POST",
          body: {
            vehicle_card_id: vehicleId,
            tariff_id: tariffId,
            client_id: chosen?.id,
            issue_location_id: location?.id,
            booking_id: booking?.id,
            actual_start: fromLocalInput(from, zone),
            planned_end: fromLocalInput(plannedEnd, zone),
          },
        }),
      );
    } catch (caught) {
      setQuoteError(caught as ApiError);
    }
  }

  // The calculation is made by itself once the vehicle, the tariff, the start and the end are
  // given (decision of 15.11.2026); without one of them there is none.
  const locationKey = location?.id ?? "";
  const clientKey = chosen?.id ?? "";
  useEffect(() => {
    if (!vehicleId || !tariffId || !from || !plannedEnd) {
      setQuote(null);
      return;
    }
    const timer = window.setTimeout(() => void calculate(), 400);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vehicleId, tariffId, from, plannedEnd, clientKey, locationKey]);
  // The units of a period that came with a booking are counted once its tariff is known.
  const tariffKey = tariff?.id ?? "";
  useEffect(() => {
    if (tariffKey && plannedEnd && !units) void recount("end", {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tariffKey]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (converting && booking) {
      const made = await toRental.run({
        expected_version: booking.version,
        client_id: chosen?.id ?? "",
        tariff_id: tariffId,
        issue_location_id: location?.id ?? "",
        ...(location ? placeBody(place) : {}),
        ...(plannedStart ? { planned_start: fromLocalInput(plannedStart, zone) } : {}),
        ...(plannedEnd ? { planned_end: fromLocalInput(plannedEnd, zone) } : {}),
        without_inspection: withoutInspection,
      });
      if (made) {
        await client.invalidateQueries({ queryKey: ["rentals", company.public_id] });
        await client.invalidateQueries({ queryKey: ["rental", company.public_id] });
        navigate(companyPath(company, `/rentals/${made.rental.id}`));
      }
      return;
    }
    const body: Record<string, unknown> = {
      vehicle_card_id: vehicleId,
      client_id: chosen?.id ?? "",
      tariff_id: tariffId,
      issue_location_id: location?.id ?? "",
      ...placeBody(place),
      actual_start: fromLocalInput(start, zone),
      // With the inspection of the rental conducted its readings are taken by the server.
      ...(inspected ? {} : { odometer, fuel }),
      inspection_conducted: false,
    };
    if (plannedEnd) body.planned_end = fromLocalInput(plannedEnd, zone);
    if (booking) body.booking_id = booking.id;
    if (unmet.length && exception.mode) {
      body.exception_mode = exception.mode;
      body.exception_reason = exception.reason;
    }
    const result = await issue.run(body);
    if (result) {
      await client.invalidateQueries({ queryKey: ["rentals", company.public_id] });
      await client.invalidateQueries({ queryKey: ["vehicles", company.public_id] });
      navigate(companyPath(company, `/rentals/${result.rental.id}`), {
        state: { warnings: result.warnings },
      });
    }
  }

  return (
    <div className="page">
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <IconButton
          icon="back"
          label={t("common.back")}
          onClick={() => (route.key === "default" ? navigate(companyPath(company, "/rentals")) : navigate(-1))}
        />
        <h1 style={{ margin: 0 }}>
          {booking?.booking && !booking.booking.direct
            ? t("bookings.to_rental_title", { number: booking.booking.number })
            : t("rentals.issue")}
        </h1>
      </div>
      <form onSubmit={submit}>
        {/* One block of short fields, as the booking is (decision of 15.11.2026): vehicle and
            its tariff; the period; the client — a rental is not issued without one; the place
            of the issue; the readings. */}
        <Card>
          <div className="stack">
            <div className="row compact">
              <div className="w-wide">
                <Field label={t("rentals.vehicle")}>
                  <select
                    value={vehicleId}
                    disabled={Boolean(booking)}
                    onChange={(e) => {
                      setVehicleId(e.target.value);
                      setTariffId("");
                      setUnits("");
                      setLocationId("");
                    }}
                  >
                    <option value="">—</option>
                    {(vehicles.data?.vehicles ?? [])
                      .filter((v) => v.id === vehicleId || (v.rental_readiness.ready && !busy.has(v.id)))
                      .map((v) => (
                        <option key={v.id} value={v.id}>
                          {v.name} · {v.vehicle_id}
                        </option>
                      ))}
                  </select>
                </Field>
              </div>
              <Field label={t("rentals.tariff")}>
                <select
                  value={tariffId}
                  onChange={(e) => {
                    setTariffId(e.target.value);
                    void recount(units ? "units" : "end", { tariffId: e.target.value });
                  }}
                >
                  <option value="">—</option>
                  {(tariffs.data?.tariffs ?? []).map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name} · {item.currency}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            {/* A rental tariff is chosen among those assigned to the vehicle (§ 13.1.2): say so
                and lead there when there is none (decision of 24.10.2026). */}
            {vehicleId && tariffs.data && tariffs.data.tariffs.length === 0 ? (
              <div className="alert warn">
                {t("rentals.no_tariff_for_vehicle")}{" "}
                <Link to={companyPath(company, "/rentals/tariffs")}>{t("settings.tariffs")}</Link>
              </div>
            ) : null}
            <div className="row compact">
              <Field label={t(converting ? "bookings.planned_start" : "rentals.actual_start")} hint={zone}>
                <input
                  type="datetime-local"
                  value={from}
                  onChange={(e) => {
                    if (converting) setPlannedStart(e.target.value);
                    else setStart(e.target.value);
                    void recount(units ? "units" : "end", { start: e.target.value });
                  }}
                />
              </Field>
              {tariff ? (
                <div className="w-short">
                  <Field label={t("bookings.units", { unit: unitShort(tariff.unit, t) })}>
                    <input
                      value={units}
                      inputMode="numeric"
                      onChange={(e) => {
                        const value = e.target.value.replace(/\D/g, "");
                        setUnits(value);
                        void recount("units", { units: value });
                      }}
                    />
                  </Field>
                </div>
              ) : null}
              <Field label={t("rentals.planned_end")}>
                <input
                  type="datetime-local"
                  value={plannedEnd}
                  onChange={(e) => {
                    setPlannedEnd(e.target.value);
                    void recount("end", { end: e.target.value });
                  }}
                />
              </Field>
            </div>
            {chosen ? (
              <div className="row compact">
                <div className="w-wide">
                  <div className="field">
                    <span>{t("rentals.client")}</span>
                    <strong style={{ display: "block" }}>{clientName(chosen)}</strong>
                    <small className="muted">{chosen.contacts.map((c) => c.value).join(", ")}</small>
                  </div>
                </div>
                <div className="beside-field">
                  <button type="button" className="secondary" onClick={() => setChosen(null)}>
                    {t("rentals.change_client")}
                  </button>
                </div>
              </div>
            ) : (
              <ClientFinder onPick={setChosen} onCreate={() => setNewClient(true)} />
            )}
            <PlaceFields
              lead={
                <Field label={t("rentals.issue_location")} hint={t("rentals.issue_location_hint")}>
                  <select value={location?.id ?? ""} onChange={(e) => setLocationId(e.target.value)}>
                    <option value="">—</option>
                    {(locations.data?.locations ?? []).map((l) => (
                      <option key={l.id} value={l.id}>
                        {l.name}
                      </option>
                    ))}
                  </select>
                </Field>
              }
              value={place}
              onChange={setPlace}
            />
            {/* The odometer and the fuel (decision of 16.11.2026): given in the inspection created
                with the rental — the issue waits for it and takes them from it; given here only
                when the rental was created without an inspection. */}
            {waiting ? (
              <div className="alert warn">
                {t("rentals.inspection_waiting")}{" "}
                <Link to={companyPath(company, `/inspections/${waiting.id}`)}>{t("rentals.open_inspection")}</Link>
              </div>
            ) : null}
            {inspected ? (
              <p>
                {t("rentals.readings_from_inspection", {
                  odometer: inspected.odometer,
                  unit: vehicle?.odometer_unit ?? "km",
                  fuel: inspected.fuel,
                })}{" "}
                <Link to={companyPath(company, `/inspections/${inspected.id}`)}>{t("rentals.open_inspection")}</Link>
              </p>
            ) : null}
            {plain ? (
              <div>
                <label className="small">
                  <input
                    type="checkbox"
                    checked={withoutInspection}
                    onChange={() => setWithoutInspection(!withoutInspection)}
                  />{" "}
                  {t("rentals.without_inspection")}
                </label>
                <p className="muted small" style={{ margin: "4px 0 0" }}>
                  {t("rentals.without_inspection_hint")}
                </p>
              </div>
            ) : null}
            {converting || inspected || waiting ? null : (
              <div className="row compact">
                <Field label={t("rentals.odometer", { unit: vehicle?.odometer_unit ?? "km" })}>
                  <input value={odometer} inputMode="decimal" onChange={(e) => setOdometer(e.target.value)} />
                </Field>
                <Field label={t("rentals.fuel")} hint={t("rentals.fuel_hint")}>
                  <input value={fuel} inputMode="decimal" onChange={(e) => setFuel(e.target.value)} />
                </Field>
              </div>
            )}
          </div>
        </Card>
        <Card title={t("rentals.preliminary")}>
          {!plannedEnd ? <p className="muted small">{t("rentals.preliminary_hint")}</p> : null}
          <ErrorMessage error={quoteError} />
          {quote ? <CalculationView calculation={quote.calculation} /> : null}
          {/* A warning repeating an unmet requirement is shown once, in the exception block. */}
          {quote ? (
            <Warnings
              warnings={quote.warnings.filter(
                (w) => !unmet.some((u) => UNMET_WARNING[u.code] === w.code) || Boolean(quote.granted),
              )}
            />
          ) : null}
          {quote?.granted ? (
            <div className="alert">
              {t("issue_rules.granted_on_booking", {
                codes: quote.granted.requirements.map((code) => t(`issue_rules.code_${code}`)).join(", "),
              })}
            </div>
          ) : null}
        </Card>
        <ExceptionChoice
          unmet={quote?.granted && !refused ? [] : unmet}
          canAuthorize={canAuthorize}
          value={exception}
          onChange={setException}
        />
        {refused ? null : <ErrorMessage error={issue.error ?? toRental.error} />}
        <div className="actions">
          <button type="submit" disabled={issue.pending || toRental.pending || !vehicleId || Boolean(waiting)}>
            {t(converting ? "bookings.to_rental" : "rentals.confirm_issue")}
          </button>
        </div>
      </form>
      {/* Outside the form of the issue: a form of its own, in a window. */}
      {!chosen && newClient ? (
        <Modal wide title={t("clients.create_title")} onClose={() => setNewClient(false)}>
          <ClientForm
            onCreated={(created) => {
              setChosen(created);
              setNewClient(false);
            }}
          />
        </Modal>
      ) : null}
    </div>
  );
}

/** Finding the client being served (client.find, § 6.2.2) by phone, email or name: one field,
 * the found clients drop down under it as a list (decision of 15.11.2026). With `onCreate` the
 * list ends with «+ New client». */
export function ClientFinder({
  onPick,
  onCreate,
  aside,
}: {
  onPick: (client: Client) => void;
  onCreate?: () => void;
  /** A button standing right of the field: «Without a client» in a booking. */
  aside?: ReactNode;
}) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const [text, setText] = useState("");
  const [found, setFound] = useState<{ client: Client; matched: string[] }[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [open, setOpen] = useState(false);

  /** A phone or an email is matched exactly (§ 6.1.11). A name: with the right to the list of
   * clients — by a part of it, in any order of the words (§ 2.8.3); without that right — only
   * the whole first and last name, tried in both orders, or the company name. */
  async function search(value = text.trim()) {
    setError(null);
    if (!value) return setFound(null);
    const words = value.split(/\s+/);
    const match = async (body: Record<string, string>) =>
      (
        await api<{ matches: { client: Client; matched: string[] }[] }>(
          `/c/${company.public_id}/clients/matches`,
          { method: "POST", body },
        )
      ).matches;
    try {
      let result: { client: Client; matched: string[] }[];
      if (value.includes("@")) result = await match({ email: value });
      else if (/\d{5,}/.test(value.replace(/\D/g, ""))) result = await match({ phone: value });
      else if (can("client.list")) {
        const list = await api<{ clients: Client[] }>(`/c/${company.public_id}/clients?q=${encodeURIComponent(value)}`);
        result = list.clients.map((client) => ({ client, matched: ["name"] }));
      } else {
        result = await match({ first_name: words[0] ?? "", last_name: words.slice(1).join(" "), company_name: value });
        if (words.length > 1) {
          const swapped = await match({ first_name: words.slice(1).join(" "), last_name: words[0] ?? "" });
          result = [...result, ...swapped.filter((s) => !result.some((r) => r.client.id === s.client.id))];
        }
      }
      setFound(result);
    } catch (caught) {
      setError(caught as ApiError);
    }
  }

  // The search runs while the name is typed, shortly after the last key.
  useEffect(() => {
    const value = text.trim();
    if (value.length < 2) return;
    const timer = window.setTimeout(() => void search(value), 350);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text]);

  const creating = onCreate && can("client.create") ? onCreate : null;
  const matches = text.trim().length >= 2 ? found : null;
  // With «Create a client» the list opens at once, before anything is typed.
  const shown = open && (matches !== null || creating !== null);

  return (
    <div className="stack">
      <div className="row compact">
        <div className="suggest w-wide">
          <Field label={t("rentals.find_client")} hint={t("rentals.find_client_hint")}>
            <input
              value={text}
              autoComplete="off"
              role="combobox"
              aria-expanded={shown}
              aria-controls="client-suggest"
              onFocus={() => setOpen(true)}
              onBlur={() => setOpen(false)}
              onChange={(e) => {
                setText(e.target.value);
                setOpen(true);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  setOpen(true);
                  void search();
                }
                if (e.key === "Escape") setOpen(false);
              }}
            />
          </Field>
          {shown ? (
            // The press must not take the focus from the field: the list would close before the click.
            <div id="client-suggest" className="suggest-list" onMouseDown={(e) => e.preventDefault()}>
              {(matches ?? []).map(({ client }) => (
                <button
                  key={client.id}
                  type="button"
                  onClick={() => {
                    setOpen(false);
                    onPick(client);
                  }}
                >
                  {clientName(client)} · {client.contacts.map((c) => c.value).join(", ")}
                </button>
              ))}
              {matches?.length === 0 ? <div className="muted small suggest-empty">{t("rentals.client_not_found")}</div> : null}
              {creating ? (
                <button
                  type="button"
                  className="suggest-create"
                  onClick={() => {
                    setOpen(false);
                    creating();
                  }}
                >
                  + {t("rentals.new_client")}
                </button>
              ) : null}
            </div>
          ) : null}
        </div>
        {aside ? <div className="beside-field">{aside}</div> : null}
      </div>
      <ErrorMessage error={error} />
    </div>
  );
}

/** The cost by the tariff (decision of 15.11.2026): units × rate = amount, in the units of the
 * tariff — no minutes for a daily one. The current and the actual cost are named; the cost
 * before the issue has no word before it. */
export function CalculationView({ calculation }: { calculation: Calculation }) {
  const { t } = useTranslation();
  const rate = calculation.rate ? ` × ${Number(calculation.rate)} ${calculation.currency}` : "";
  return (
    <p>
      {calculation.kind === "preliminary" ? "" : `${t(`rentals.calc_${calculation.kind}`)}: `}
      {calculation.units} {unitShort(calculation.unit, t)}
      {rate}
      {calculation.free_minutes ? ` (${t("charges.free_minutes", { value: calculation.free_minutes })})` : ""} ={" "}
      <strong>
        {calculation.base_amount} {calculation.currency}
      </strong>
    </p>
  );
}

export function Warnings({ warnings }: { warnings: Warning[] }) {
  const { t } = useTranslation();
  return (
    <>
      {warnings.map((warning) => (
        <div key={warning.code} className="alert warn">
          {t(`warnings.${warning.code.toLowerCase()}`, {
            amounts: Object.entries(warning.amounts ?? {})
              .map(([currency, amount]) => `${amount} ${currency}`)
              .join(", "),
            documents: (warning.documents ?? [])
              .map((d) => d.template ?? d.kind_name ?? t(`documents.kind_${d.kind_code ?? ""}`))
              .join(", "),
          })}
        </div>
      ))}
    </>
  );
}
