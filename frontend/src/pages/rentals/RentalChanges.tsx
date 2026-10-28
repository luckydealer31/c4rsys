import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "../../api/queries";
import type { Rental } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { ErrorMessage, Field } from "../../components/ui";
import { useCan, useCompany } from "../../layout/CompanyShell";
import { fromLocalInput, toLocalInput } from "../../time";
import { useLocations } from "../settings/LocationsPage";

type Done = (next?: Rental) => Promise<void>;

/** Extension (§ 14.5.2, § 14.5.8; decisions П2–П4): one action moves the planned end later or
 * earlier or sets it for the first time; the client's consent is marked with a comment. */
export function ExtendForm({ rental, onDone }: { rental: Rental; onDone: Done }) {
  const { t } = useTranslation();
  const company = useCompany();
  const zone = rental.calc_timezone;
  const [end, setEnd] = useState(() =>
    toLocalInput(rental.planned_end ? new Date(rental.planned_end) : new Date(Date.now() + 86_400_000), zone),
  );
  const [consent, setConsent] = useState(false);
  const [comment, setComment] = useState("");
  const change = useCommand<Record<string, unknown>, { rental: Rental }>(
    `/c/${company.public_id}/rentals/${rental.id}/planned-end`,
  );

  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await change.run({
      planned_end: fromLocalInput(end, zone),
      client_consent: consent,
      consent_comment: comment,
      expected_version: rental.version,
    });
    if (result) {
      setConsent(false);
      setComment("");
      await onDone(result.rental);
    }
  }

  return (
    <form onSubmit={submit}>
      <p className="muted small">{t("changes.extend_hint")}</p>
      {rental.overdue ? <p className="alert warn">{t("changes.overdue_cost")}</p> : null}
      <div className="row">
        <Field label={t("changes.new_planned_end")} hint={zone}>
          <input type="datetime-local" value={end} onChange={(e) => setEnd(e.target.value)} />
        </Field>
        <Field label={t("changes.consent_comment")}>
          <input
            value={comment}
            placeholder={t("changes.consent_placeholder")}
            onChange={(e) => setComment(e.target.value)}
          />
        </Field>
      </div>
      <label className="small">
        <input type="checkbox" checked={consent} onChange={() => setConsent(!consent)} /> {t("changes.client_consent")}
      </label>
      <ErrorMessage error={change.error} />
      <div className="actions">
        <button type="submit" disabled={change.pending || !consent || !comment.trim()}>
          {t("changes.change_planned_end")}
        </button>
      </div>
    </form>
  );
}

/** Another tariff from the start of the rental (§ 13.6.2–13.6.4; decisions П6, П7). */
export function TariffChange({ rental, onDone }: { rental: Rental; onDone: Done }) {
  const { t } = useTranslation();
  const company = useCompany();
  const tariffs = useApi<{ tariffs: { id: string; name: string; currency: string }[] }>(
    ["vehicle-tariffs", company.public_id, rental.vehicle_card_id],
    `/c/${company.public_id}/vehicles/${rental.vehicle_card_id}/tariffs`,
  );
  const options = (tariffs.data?.tariffs ?? []).filter((x) => x.id !== rental.tariff_id);
  const [tariffId, setTariffId] = useState("");
  const change = useCommand<Record<string, unknown>, { rental: Rental }>(
    `/c/${company.public_id}/rentals/${rental.id}/tariff`,
  );

  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await change.run({ tariff_id: tariffId, expected_version: rental.version });
    if (result) {
      setTariffId("");
      await onDone(result.rental);
    }
  }

  return (
    <form onSubmit={submit}>
      <p className="muted small">{t("changes.tariff_hint")}</p>
      <div className="row">
        <Field label={t("changes.new_tariff")}>
          <select value={tariffId} onChange={(e) => setTariffId(e.target.value)}>
            <option value="">—</option>
            {options.map((x) => (
              <option key={x.id} value={x.id}>
                {x.name} · {x.currency}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <ErrorMessage error={change.error} />
      <div className="actions">
        <button type="submit" disabled={change.pending || !tariffId}>
          {t("changes.change_tariff")}
        </button>
      </div>
    </form>
  );
}

/** Reopening a rental with completed formalization, with a reason (§ 13.6.5). */
export function ReopenRental({ rental, onDone }: { rental: Rental; onDone: Done }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const [reason, setReason] = useState("");
  const reopen = useCommand<Record<string, unknown>, { rental: Rental }>(
    `/c/${company.public_id}/rentals/${rental.id}/reopen`,
  );
  if (!can("rental.reopen")) return null;

  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await reopen.run({ reason, expected_version: rental.version });
    if (result) {
      setReason("");
      await onDone(result.rental);
    }
  }

  return (
    <form onSubmit={submit}>
      <div className="row">
        <Field label={t("changes.reopen_reason")}>
          <input value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
      </div>
      <ErrorMessage error={reopen.error} />
      <div className="actions">
        <button type="submit" className="secondary" disabled={reopen.pending || !reason.trim()}>
          {t("changes.reopen")}
        </button>
      </div>
    </form>
  );
}

/** Correction of a reopened rental (decision П11): its actual period and return place. */
export function CorrectionForm({ rental, onDone }: { rental: Rental; onDone: Done }) {
  const { t } = useTranslation();
  const company = useCompany();
  const locations = useLocations();
  const zone = rental.calc_timezone;
  const [start, setStart] = useState(() => toLocalInput(new Date(rental.actual_start ?? Date.now()), zone));
  const [end, setEnd] = useState(() => toLocalInput(new Date(rental.actual_end ?? Date.now()), zone));
  const [locationId, setLocationId] = useState(rental.return_location_id ?? "");
  const [placeKind, setPlaceKind] = useState<"location" | "other">(rental.return_place_kind ?? "location");
  const [placeNote, setPlaceNote] = useState(rental.return_place_note);
  const correct = useCommand<Record<string, unknown>, { rental: Rental }>(
    `/c/${company.public_id}/rentals/${rental.id}/correct`,
  );

  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await correct.run({
      actual_start: fromLocalInput(start, zone),
      actual_end: fromLocalInput(end, zone),
      return_location_id: locationId,
      place_kind: placeKind,
      place_note: placeNote,
      expected_version: rental.version,
    });
    if (result) await onDone(result.rental);
  }

  return (
    <form onSubmit={submit}>
      <p className="alert warn">{t("changes.reopened")}</p>
      <div className="row">
        <Field label={t("rentals.actual_start")} hint={zone}>
          <input type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} />
        </Field>
        <Field label={t("rentals.actual_end")} hint={zone}>
          <input type="datetime-local" value={end} onChange={(e) => setEnd(e.target.value)} />
        </Field>
      </div>
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
      <ErrorMessage error={correct.error} />
      <div className="actions">
        <button type="submit" disabled={correct.pending}>
          {t("changes.correct")}
        </button>
      </div>
    </form>
  );
}
