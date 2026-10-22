import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useSearchParams } from "react-router";

import { api, ApiError } from "../../api/client";
import { useApi } from "../../api/queries";
import type { Calculation, Client, PriceRange, Rental, Warning } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { fromLocalInput, toLocalInput } from "../../time";
import { ClientForm, clientName } from "../clients/ClientsPage";
import { useLocations } from "../settings/LocationsPage";
import { useRentals, useVehicles } from "../vehicles/VehiclesPage";

type VehicleTariff = { id: string; name: string; currency: string; ranges: PriceRange[] };

/** Issue without a booking (§ 14.2–14.4): vehicle, client (search or create on the spot), tariff
 * of the vehicle, issue location and «in location / other», actual time (now by default),
 * odometer, fuel, planned end. The preliminary calculation and warnings come before confirming
 * (§ 13.6.6). */
export function IssuePage() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const navigate = useNavigate();
  const client = useQueryClient();
  const [params] = useSearchParams();
  const vehicles = useVehicles();
  const locations = useLocations();
  const rentals = useRentals(can("rental.view"));
  const busy = new Set((rentals.data?.rentals ?? []).filter((r) => r.state === "active").map((r) => r.vehicle_card_id));
  const [vehicleId, setVehicleId] = useState(params.get("vehicle") ?? "");
  const vehicle = vehicles.data?.vehicles.find((v) => v.id === vehicleId);
  const [locationId, setLocationId] = useState("");
  const location = (locations.data?.locations ?? []).find((l) => l.id === (locationId || vehicle?.location_id));
  const zone = location?.timezone ?? company.timezone ?? "UTC";
  const tariffs = useApi<{ tariffs: VehicleTariff[] }>(
    ["vehicle-tariffs", company.public_id, vehicleId],
    `/c/${company.public_id}/vehicles/${vehicleId}/tariffs`,
    Boolean(vehicleId),
  );
  const [chosen, setChosen] = useState<Client | null>(null);
  const [newClient, setNewClient] = useState(false);
  const [tariffId, setTariffId] = useState("");
  const [placeKind, setPlaceKind] = useState<"location" | "other">("location");
  const [placeNote, setPlaceNote] = useState("");
  const [start, setStart] = useState(() => toLocalInput(new Date(), zone));
  const [plannedEnd, setPlannedEnd] = useState("");
  const [plannedReturn, setPlannedReturn] = useState("");
  const [odometer, setOdometer] = useState("");
  const [fuel, setFuel] = useState("");
  const [conducted, setConducted] = useState(false);
  const [quote, setQuote] = useState<{ calculation: Calculation; warnings: Warning[] } | null>(null);
  const [quoteError, setQuoteError] = useState<ApiError | null>(null);
  const issue = useCommand<Record<string, unknown>, { rental: Rental; warnings: Warning[] }>(
    `/c/${company.public_id}/rentals/issue`,
  );

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
      place_kind: placeKind,
      place_note: placeNote,
      actual_start: fromLocalInput(start, zone),
      odometer,
      fuel,
      inspection_conducted: conducted,
    };
    if (plannedEnd) body.planned_end = fromLocalInput(plannedEnd, zone);
    if (plannedReturn) body.planned_return_location_id = plannedReturn;
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
      <h1>{t("rentals.issue")}</h1>
      <form onSubmit={submit}>
        <Card title={t("rentals.vehicle_and_client")}>
          <div className="row">
            <Field label={t("rentals.vehicle")}>
              <select
                value={vehicleId}
                onChange={(e) => {
                  setVehicleId(e.target.value);
                  setTariffId("");
                  setLocationId("");
                }}
              >
                <option value="">—</option>
                {(vehicles.data?.vehicles ?? [])
                  .filter((v) => v.rental_readiness.ready && !busy.has(v.id))
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
            <Field label={t("rentals.place")}>
              <select value={placeKind} onChange={(e) => setPlaceKind(e.target.value as "location" | "other")}>
                <option value="location">{t("rentals.place_location")}</option>
                <option value="other">{t("rentals.place_other")}</option>
              </select>
            </Field>
            {placeKind === "other" ? (
              <Field label={t("rentals.place_note")}>
                <input value={placeNote} onChange={(e) => setPlaceNote(e.target.value)} />
              </Field>
            ) : null}
          </div>
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
          <p className="muted small">{t("rentals.inspection_policy")}</p>
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
          {quote ? <Warnings warnings={quote.warnings} /> : null}
        </Card>
        <ErrorMessage error={issue.error} />
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
function ClientFinder({ onPick }: { onPick: (client: Client) => void }) {
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
      {t(`rentals.calc_${calculation.kind}`)}: {calculation.billable_minutes} {t("rentals.minutes")} →{" "}
      {calculation.units} {t("rentals.days")} → {calculation.rate ? Number(calculation.rate) : "—"} →{" "}
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
          })}
        </div>
      ))}
    </>
  );
}
