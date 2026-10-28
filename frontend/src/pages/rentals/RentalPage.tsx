import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useLocation, useParams } from "react-router";

import type { ApiError } from "../../api/client";
import { useApi } from "../../api/queries";
import type { Client, InspectionView, JournalEntry, Rental, Warning } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { formatMoment, fromLocalInput, toLocalInput } from "../../time";
import { clientName } from "../clients/ClientsPage";
import { JournalTable } from "../settings/EmployeesPage";
import { useLocations } from "../settings/LocationsPage";
import { useVehicles } from "../vehicles/VehiclesPage";
import { BookingCard } from "./Booking";
import { DepositCard } from "./Deposit";
import { IssueExceptionView, placeText } from "./IssueRules";
import { FreeTime, ManualCharges, MileageView } from "./RentalCharges";
import { CorrectionForm, ExtendForm, ReopenRental, TariffChange } from "./RentalChanges";
import { AnnulRental, TransferRental } from "./RentalOrder";
import { RentalServices } from "./Services";
import { CalculationView, Warnings } from "./IssuePage";

type RentalResponse = { rental: Rental };

/** Rental card (STAGE1_SCREENS): returned, formalization and settlement are three separate
 * blocks (§ 14.5.6); calculation breakdown; issue and return readings with the fuel comparison
 * (§ 13.5.1); history tab. */
export function RentalPage() {
  const { t, i18n } = useTranslation();
  const { id = "" } = useParams();
  const company = useCompany();
  const can = useCan();
  const issued = useLocation().state as { warnings?: Warning[] } | null;
  const queryClient = useQueryClient();
  const key = ["rental", company.public_id, id];
  const rental = useApi<RentalResponse>(key, `/c/${company.public_id}/rentals/${id}`);
  const history = useApi<{ history: JournalEntry[] }>(
    ["rental-history", company.public_id, id],
    `/c/${company.public_id}/rentals/${id}/history`,
  );
  const vehicles = useVehicles();
  const locations = useLocations();
  const data = rental.data?.rental;
  const client = useApi<{ client: Client }>(
    ["client", company.public_id, data?.client_id ?? ""],
    `/c/${company.public_id}/clients/${data?.client_id ?? ""}`,
    Boolean(data) && (can("client.find") || can("client.list")),
  );
  const [showHistory, setShowHistory] = useState(false);
  if (rental.isLoading) {
    return (
      <div className="page">
        <Loading />
      </div>
    );
  }
  if (!data) {
    return (
      <div className="page">
        <ErrorMessage error={rental.error as ApiError} />
      </div>
    );
  }
  const current = data;
  const locationName = new Map((locations.data?.locations ?? []).map((l) => [l.id, l.name]));
  const vehicle = vehicles.data?.vehicles.find((v) => v.id === current.vehicle_card_id);
  const zone = current.calc_timezone;
  const when = (value: string | null) => formatMoment(value, zone, i18n.language);
  // A booking has no return, formalization or settlement yet (§ 14.1); a cancelled one never.
  const operated = current.state !== "booked" && current.state !== "cancelled";
  // Decisions П6, П11: the tariff changes until the formalization is completed.
  const unfinished =
    (current.state === "active" || current.state === "returned") && !current.formalization.completed_at;

  async function refresh(next?: Rental) {
    if (next) queryClient.setQueryData<RentalResponse>(key, { rental: next });
    await queryClient.invalidateQueries({ queryKey: key });
    await queryClient.invalidateQueries({ queryKey: ["rental-history", company.public_id, id] });
    await queryClient.invalidateQueries({ queryKey: ["rentals", company.public_id] });
    await queryClient.invalidateQueries({ queryKey: ["vehicles", company.public_id] });
  }

  return (
    <div className="page">
      <h1>
        {vehicle?.name ?? t("rentals.rental")} · {t(`rentals.state_${current.state}`)}
      </h1>
      <Warnings warnings={issued?.warnings ?? []} />
      {current.overdue ? <div className="alert warn">{t("rentals.overdue")}</div> : null}
      <Card title={t("rentals.summary")}>
        <dl className="facts">
          <dt>{t("rentals.vehicle")}</dt>
          <dd>{vehicle ? `${vehicle.name} · ${vehicle.vehicle_id}` : "—"}</dd>
          <dt>{t("rentals.client")}</dt>
          <dd>
            {client.data ? (
              <Link to={companyPath(company, `/clients/${current.client_id}`)}>{clientName(client.data.client)}</Link>
            ) : (
              "—"
            )}
          </dd>
          <dt>{t("rentals.tariff")}</dt>
          <dd>
            {current.tariff_snapshot ? `${current.tariff_snapshot.name} · ${current.tariff_snapshot.currency}` : "—"}
          </dd>
          <dt>{t("rentals.issue_location")}</dt>
          <dd>
            {locationName.get(current.issue_location_id ?? "") ?? "—"}
            {current.issue_place_kind === "other" ? ` · ${t("rentals.place_other")}: ${placeText(current)}` : ""}
          </dd>
          <dt>{t("rentals.actual_start")}</dt>
          <dd>{when(current.actual_start)}</dd>
          <dt>{t("rentals.planned_end")}</dt>
          <dd>{when(current.planned_end)}</dd>
          <dt>{t("rentals.planned_return_location")}</dt>
          <dd>{locationName.get(current.planned_return_location_id ?? "") ?? "—"}</dd>
          <dt>{t("rentals.order")}</dt>
          <dd>
            <Link to={companyPath(company, `/orders/${current.order_id}`)}>{t("rentals.open_order")}</Link>
          </dd>
        </dl>
        <p className="muted small">{t("rentals.time_zone", { zone })}</p>
      </Card>

      {current.issue_exception && current.state !== "booked" ? (
        <Card title={t("issue_rules.exception_title")}>
          <IssueExceptionView rental={current} onDone={refresh} />
        </Card>
      ) : null}

      {current.booking ? (
        <Card title={t("bookings.title", { number: current.booking.number })}>
          <BookingCard rental={current} onDone={refresh} />
        </Card>
      ) : null}

      {current.state === "active" && can("rental.extend") ? (
        <Card title={t("changes.extend_title")}>
          <ExtendForm rental={current} onDone={refresh} />
        </Card>
      ) : null}

      {unfinished && can("rental.change_terms") ? (
        <Card title={t("changes.tariff_title")}>
          <TariffChange rental={current} onDone={refresh} />
        </Card>
      ) : null}

      <Card title={t("rentals.calculation")}>
        {current.calculation ? (
          <CalculationView calculation={current.calculation} />
        ) : (
          <p className="muted">{t("bookings.no_calculation")}</p>
        )}
      </Card>

      {current.state !== "cancelled" || current.services?.length ? (
        <Card title={t("services.title")}>
          {current.services_not_allowed?.length ? (
            <p className="alert warn">{t("changes.services_not_allowed")}</p>
          ) : null}
          <RentalServices rental={current} onDone={refresh} />
        </Card>
      ) : null}

      {operated ? (
        <>
          <Card title={t("charges.free_title")}>
            <FreeTime rental={current} onDone={refresh} />
          </Card>
          <Card title={t("charges.mileage_title")}>
            <MileageView rental={current} />
          </Card>
          <Card title={t("charges.manual_title")}>
            <ManualCharges rental={current} onDone={refresh} />
          </Card>
        </>
      ) : null}

      <Card title={t("deposit.title")}>
        <DepositCard rental={current} onDone={() => refresh()} />
      </Card>

      {operated ? (
      <>
      <Card title={t("rentals.block_returned")}>
        {current.state === "active" ? (
          can("rental.accept_return") ? (
            <ReturnForm rental={current} onDone={refresh} />
          ) : (
            <p className="muted">{t("rentals.not_returned")}</p>
          )
        ) : (
          <dl className="facts">
            <dt>{t("rentals.actual_end")}</dt>
            <dd>{when(current.actual_end)}</dd>
            <dt>{t("rentals.return_location")}</dt>
            <dd>
              {locationName.get(current.return_location_id ?? "") ?? "—"}
              {current.return_place_kind === "other" ? ` · ${current.return_place_note}` : ""}
            </dd>
          </dl>
        )}
        <Readings rental={current} onDone={refresh} />
      </Card>

      {unfinished && current.state === "returned" && current.reopened_at && can("rental.reopen") ? (
        <Card title={t("changes.correct_title")}>
          <CorrectionForm key={current.version} rental={current} onDone={refresh} />
        </Card>
      ) : null}

      <Card title={t("rentals.block_formalization")}>
        <Formalization rental={current} onDone={refresh} when={when} />
      </Card>

      <Card title={t("rentals.block_settlement")}>
        <p>
          {t("rentals.settlement_in_order")}{" "}
          <Link to={companyPath(company, `/orders/${current.order_id}`)}>{t("rentals.open_order")}</Link>
        </p>
      </Card>
      </>
      ) : null}

      {["booked", "active", "returned"].includes(current.state) && can("rental.transfer") ? (
        <Card title={t("rental_order.transfer_title")}>
          <TransferRental rental={current} onDone={refresh} />
        </Card>
      ) : null}

      {(current.state === "active" || current.state === "returned") && can("rental.annul") ? (
        <Card title={t("rental_order.annul_title")}>
          <AnnulRental rental={current} onDone={refresh} />
        </Card>
      ) : null}

      <div className="actions">
        <button type="button" className="secondary" onClick={() => setShowHistory(!showHistory)}>
          {t("rentals.history")}
        </button>
      </div>
      {showHistory ? (
        <Card title={t("rentals.history")}>
          {history.isLoading ? <Loading /> : null}
          <ErrorMessage error={history.error as ApiError} />
          <JournalTable entries={history.data?.history ?? []} timeZone={zone} />
        </Card>
      ) : null}
    </div>
  );
}

/** Acceptance of the return (§ 14.5.1–14.5.5). Readings may be left empty and entered later. */
function ReturnForm({ rental, onDone }: { rental: Rental; onDone: (next?: Rental) => Promise<void> }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const locations = useLocations();
  const [locationId, setLocationId] = useState(rental.planned_return_location_id ?? rental.issue_location_id ?? "");
  const zone = locations.data?.locations.find((l) => l.id === locationId)?.timezone ?? rental.calc_timezone;
  const [end, setEnd] = useState(() => toLocalInput(new Date(), rental.calc_timezone));
  const [placeKind, setPlaceKind] = useState<"location" | "other">("location");
  const [placeNote, setPlaceNote] = useState("");
  const [odometer, setOdometer] = useState("");
  const [fuel, setFuel] = useState("");
  const [conducted, setConducted] = useState(false);
  const accept = useCommand<Record<string, unknown>, { rental: Rental }>(
    `/c/${company.public_id}/rentals/${rental.id}/return`,
  );

  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await accept.run({
      return_location_id: locationId,
      place_kind: placeKind,
      place_note: placeNote,
      actual_end: fromLocalInput(end, zone),
      odometer,
      fuel,
      inspection_conducted: conducted,
      expected_version: rental.version,
    });
    if (result) await onDone(result.rental);
  }

  return (
    <form onSubmit={submit}>
      <div className="row">
        <Field label={t("rentals.return_location")}>
          <select value={locationId} onChange={(e) => setLocationId(e.target.value)}>
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
        <Field label={t("rentals.actual_end")} hint={zone}>
          <input type="datetime-local" value={end} onChange={(e) => setEnd(e.target.value)} />
        </Field>
        <Field label={t("rentals.odometer", { unit: "" })} hint={t("rentals.readings_later")}>
          <input value={odometer} inputMode="decimal" onChange={(e) => setOdometer(e.target.value)} />
        </Field>
        <Field label={t("rentals.fuel")} hint={t("rentals.fuel_hint")}>
          <input value={fuel} inputMode="decimal" onChange={(e) => setFuel(e.target.value)} />
        </Field>
      </div>
      {can("inspection.conduct") ? (
        <label className="small">
          <input type="checkbox" checked={conducted} onChange={() => setConducted(!conducted)} />{" "}
          {t("rentals.inspection_conducted")}
        </label>
      ) : null}
      <ErrorMessage error={accept.error} />
      <div className="actions">
        <button type="submit" disabled={accept.pending}>
          {t("rentals.accept_return")}
        </button>
      </div>
    </form>
  );
}

/** Issue and return readings; return readings left empty are entered here later (§ 14.5.5). */
function Readings({ rental, onDone }: { rental: Rental; onDone: (next?: Rental) => Promise<void> }) {
  const { t } = useTranslation();
  const { issue, return: back, fuel_differs } = rental.inspections;
  return (
    <>
      <table>
        <thead>
          <tr>
            <th>{t("rentals.inspection")}</th>
            <th>{t("rentals.odometer", { unit: "" })}</th>
            <th>{t("rentals.fuel")}</th>
            <th>{t("rentals.inspection_state")}</th>
          </tr>
        </thead>
        <tbody>
          <ReadingRow label={t("rentals.inspection_issue")} item={issue} />
          <ReadingRow label={t("rentals.inspection_return")} item={back} />
        </tbody>
      </table>
      {fuel_differs !== null ? (
        <p className={fuel_differs ? "alert warn" : "muted small"}>
          {t(fuel_differs ? "rentals.fuel_differs" : "rentals.fuel_same")}
        </p>
      ) : null}
      {back && back.state === "draft" ? <LateReadings inspection={back} onDone={onDone} /> : null}
    </>
  );
}

function ReadingRow({ label, item }: { label: string; item: InspectionView | null }) {
  const { t } = useTranslation();
  return (
    <tr>
      <td>{label}</td>
      <td>{item?.odometer ?? "—"}</td>
      <td>{item?.fuel ?? "—"}</td>
      <td>
        {item ? `${t(`rentals.inspection_${item.state}`)} · ${t(`rentals.check_${item.check_status}`)}` : "—"}
      </td>
    </tr>
  );
}

function LateReadings({
  inspection,
  onDone,
}: {
  inspection: InspectionView;
  onDone: (next?: Rental) => Promise<void>;
}) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const [odometer, setOdometer] = useState("");
  const [fuel, setFuel] = useState("");
  const [conducted, setConducted] = useState(false);
  const save = useCommand<Record<string, unknown>, { rental: Rental }>(
    `/c/${company.public_id}/inspections/${inspection.id}/save`,
  );
  if (!can("rental.accept_return")) return null;

  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await save.run({ odometer, fuel, inspection_conducted: conducted });
    if (result) await onDone(result.rental);
  }

  return (
    <form onSubmit={submit}>
      <h3>{t("rentals.enter_return_readings")}</h3>
      <div className="row">
        <Field label={t("rentals.odometer", { unit: "" })}>
          <input value={odometer} inputMode="decimal" onChange={(e) => setOdometer(e.target.value)} />
        </Field>
        <Field label={t("rentals.fuel")}>
          <input value={fuel} inputMode="decimal" onChange={(e) => setFuel(e.target.value)} />
        </Field>
      </div>
      {can("inspection.conduct") ? (
        <label className="small">
          <input type="checkbox" checked={conducted} onChange={() => setConducted(!conducted)} />{" "}
          {t("rentals.inspection_conducted")}
        </label>
      ) : null}
      <ErrorMessage error={save.error} />
      <div className="actions">
        <button type="submit" disabled={save.pending}>
          {t("rentals.save_readings")}
        </button>
      </div>
    </form>
  );
}

function Formalization({
  rental,
  onDone,
  when,
}: {
  rental: Rental;
  onDone: (next?: Rental) => Promise<void>;
  when: (value: string | null) => string;
}) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const complete = useCommand<Record<string, unknown>, { rental: Rental }>(
    `/c/${company.public_id}/rentals/${rental.id}/complete-formalization`,
  );
  if (rental.formalization.completed_at) {
    return (
      <>
        <p>{t("rentals.formalization_done", { when: when(rental.formalization.completed_at) })}</p>
        <ReopenRental rental={rental} onDone={onDone} />
      </>
    );
  }
  if (rental.state !== "returned") return <p className="muted">{t("rentals.formalization_after_return")}</p>;
  const missing = rental.formalization.missing;
  return (
    <>
      {missing.length ? (
        <ul>
          {missing.map((code) => (
            <li key={code}>{t(`rentals.missing_${code}`)}</li>
          ))}
        </ul>
      ) : (
        <p>{t("rentals.formalization_ready")}</p>
      )}
      <ErrorMessage error={complete.error} />
      {can("rental.complete_formalization") ? (
        <div className="actions">
          <button
            type="button"
            disabled={complete.pending || missing.length > 0}
            onClick={() =>
              void complete.run({ expected_version: rental.version }).then((r) => (r ? onDone(r.rental) : undefined))
            }
          >
            {t("rentals.complete_formalization")}
          </button>
        </div>
      ) : null}
    </>
  );
}
