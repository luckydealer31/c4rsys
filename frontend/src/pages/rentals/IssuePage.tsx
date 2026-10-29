import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useSearchParams } from "react-router";

import { api, ApiError } from "../../api/client";
import { useApi } from "../../api/queries";
import type {
  Calculation,
  Client,
  IssueException,
  IssueRequirement,
  PriceRange,
  Rental,
  Warning,
} from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
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

type VehicleTariff = { id: string; name: string; currency: string; ranges: PriceRange[] };

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
  const clientId = booking.data?.rental.client_id ?? "";
  const bookedClient = useApi<{ client: Client }>(
    ["client", company.public_id, clientId],
    `/c/${company.public_id}/clients/${clientId}`,
    Boolean(clientId),
  );
  if (bookingId && !booking.data) {
    return <div className="page">{booking.error ? <ErrorMessage error={booking.error as ApiError} /> : <Loading />}</div>;
  }
  if (clientId && bookedClient.isLoading) {
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
  const [plannedReturn, setPlannedReturn] = useState(booking?.planned_return_location_id ?? "");
  const [odometer, setOdometer] = useState("");
  const [fuel, setFuel] = useState("");
  const [conducted, setConducted] = useState(false);
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
            actual_start: fromLocalInput(start, zone),
            planned_end: fromLocalInput(plannedEnd, zone),
          },
        }),
      );
    } catch (caught) {
      setQuoteError(caught as ApiError);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const body: Record<string, unknown> = {
      vehicle_card_id: vehicleId,
      client_id: chosen?.id ?? "",
      tariff_id: tariffId,
      issue_location_id: location?.id ?? "",
      ...placeBody(place),
      actual_start: fromLocalInput(start, zone),
      odometer,
      fuel,
      inspection_conducted: conducted,
    };
    if (plannedEnd) body.planned_end = fromLocalInput(plannedEnd, zone);
    if (plannedReturn) body.planned_return_location_id = plannedReturn;
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
      <h1>{booking?.booking ? t("bookings.issue_title", { number: booking.booking.number }) : t("rentals.issue")}</h1>
      <form onSubmit={submit}>
        <Card title={t("rentals.vehicle_and_client")}>
          <div className="row">
            <Field label={t("rentals.vehicle")}>
              <select
                value={vehicleId}
                disabled={Boolean(booking)}
                onChange={(e) => {
                  setVehicleId(e.target.value);
                  setTariffId("");
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
          </div>
          {/* A rental tariff is chosen among those assigned to the vehicle (§ 13.1.2): say so
              and lead there when there is none (decision of 24.10.2026). */}
          {vehicleId && tariffs.data && tariffs.data.tariffs.length === 0 ? (
            <div className="alert warn">
              {t("rentals.no_tariff_for_vehicle")}{" "}
              <Link to={companyPath(company, "/rentals/tariffs")}>{t("settings.tariffs")}</Link>
            </div>
          ) : null}
          <h3 style={{ marginTop: 16 }}>{t("rentals.client")}</h3>
          {chosen ? (
            <p>
              <strong>{clientName(chosen)}</strong> · {chosen.contacts.map((c) => c.value).join(", ")}{" "}
              <button type="button" className="secondary" onClick={() => setChosen(null)}>
                {t("rentals.change_client")}
              </button>
            </p>
          ) : (
            <ClientFinder onPick={setChosen} />
          )}
          {!chosen && can("client.create") ? (
            <div className="actions">
              <button type="button" className="secondary" onClick={() => setNewClient(!newClient)}>
                {t("rentals.new_client")}
              </button>
            </div>
          ) : null}
        </Card>
        {!chosen && newClient ? (
          <ClientForm
            onCreated={(created) => {
              setChosen(created);
              setNewClient(false);
            }}
          />
        ) : null}
        <Card title={t("rentals.issue_details")}>
          <div className="row">
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
          </div>
          <PlaceFields value={place} onChange={setPlace} />
          <div className="row">
            <Field label={t("rentals.actual_start")} hint={zone}>
              <input type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} />
            </Field>
            <Field label={t("rentals.odometer", { unit: vehicle?.odometer_unit ?? "km" })}>
              <input value={odometer} inputMode="decimal" onChange={(e) => setOdometer(e.target.value)} />
            </Field>
            <Field label={t("rentals.fuel")} hint={t("rentals.fuel_hint")}>
              <input value={fuel} inputMode="decimal" onChange={(e) => setFuel(e.target.value)} />
            </Field>
          </div>
          <div className="row">
            <Field label={t("rentals.planned_end")}>
              <input type="datetime-local" value={plannedEnd} onChange={(e) => setPlannedEnd(e.target.value)} />
            </Field>
            <Field label={t("rentals.planned_return_location")}>
              <select value={plannedReturn} onChange={(e) => setPlannedReturn(e.target.value)}>
                <option value="">—</option>
                {(locations.data?.locations ?? []).map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.name}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          {can("inspection.conduct") ? (
            <label className="small">
              <input type="checkbox" checked={conducted} onChange={() => setConducted(!conducted)} />{" "}
              {t("rentals.inspection_conducted")}
            </label>
          ) : null}
          <p className="muted small">
            {t(quote?.inspection_required ? "issue_rules.inspection_required_hint" : "rentals.inspection_policy")}
          </p>
        </Card>
        <Card title={t("rentals.preliminary")}>
          <div className="actions" style={{ marginTop: 0 }}>
            <button
              type="button"
              className="secondary"
              disabled={!vehicleId || !tariffId || !plannedEnd}
              onClick={() => void calculate()}
            >
              {t("rentals.calculate")}
            </button>
          </div>
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
        {refused ? null : <ErrorMessage error={issue.error} />}
        <div className="actions">
          <button type="submit" disabled={issue.pending || !vehicleId}>
            {t("rentals.confirm_issue")}
          </button>
        </div>
      </form>
    </div>
  );
}

/** Finding the client being served (client.find, § 6.2.2) by phone, email or name. */
export function ClientFinder({ onPick }: { onPick: (client: Client) => void }) {
  const { t } = useTranslation();
  const company = useCompany();
  const [text, setText] = useState("");
  const [found, setFound] = useState<{ client: Client; matched: string[] }[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  async function search() {
    setError(null);
    const value = text.trim();
    const words = value.split(/\s+/);
    const body: Record<string, string> = value.includes("@")
      ? { email: value }
      : /\d{5,}/.test(value.replace(/\D/g, ""))
        ? { phone: value }
        : { first_name: words[0] ?? "", last_name: words.slice(1).join(" "), company_name: value };
    try {
      setFound(
        (
          await api<{ matches: { client: Client; matched: string[] }[] }>(
            `/c/${company.public_id}/clients/matches`,
            { method: "POST", body },
          )
        ).matches,
      );
    } catch (caught) {
      setError(caught as ApiError);
    }
  }

  return (
    <div className="stack">
      <div className="row">
        <Field label={t("rentals.find_client")} hint={t("rentals.find_client_hint")}>
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                void search();
              }
            }}
          />
        </Field>
      </div>
      <div className="actions" style={{ marginTop: 0 }}>
        <button type="button" className="secondary" disabled={!text.trim()} onClick={() => void search()}>
          {t("rentals.search")}
        </button>
      </div>
      <ErrorMessage error={error} />
      {found?.length === 0 ? <p className="muted small">{t("rentals.client_not_found")}</p> : null}
      {(found ?? []).map(({ client }) => (
        <button key={client.id} type="button" className="secondary" onClick={() => onPick(client)}>
          {clientName(client)} · {client.contacts.map((c) => c.value).join(", ")}
        </button>
      ))}
    </div>
  );
}

/** Breakdown: minutes → days → rate → amount (STAGE1_SCREENS, § 13.2). */
export function CalculationView({ calculation }: { calculation: Calculation }) {
  const { t } = useTranslation();
  return (
    <p>
      {t(`rentals.calc_${calculation.kind}`)}: {calculation.billable_minutes} {t("rentals.minutes")}
      {calculation.free_minutes ? ` (${t("charges.free_minutes", { value: calculation.free_minutes })})` : ""} →{" "}
      {calculation.units} {unitShort(calculation.unit, t)} → {calculation.rate ? Number(calculation.rate) : "—"} →{" "}
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
