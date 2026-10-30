/** Fields of an inspection shared by its creation and its filling: place (§ 10.1.2), second
 * participant (§ 10.1.3), performer and actual moment (§ 10.1.5–10.1.6). */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { Employee, Inspection } from "../../api/types";
import { Field } from "../../components/ui";
import { fromLocalInput, toLocalInput } from "../../time";
import { useContractors } from "../service/hooks";
import { useEmployees } from "../settings/EmployeesPage";
import { useLocations } from "../settings/LocationsPage";

export type Details = {
  performed_at: string; // datetime-local in `zone`; "" — not changed
  place_kind: "location" | "other";
  place_location_id: string;
  place_note: string;
  place_address: string;
  place_lat: string;
  place_lng: string;
  participant_kind: "" | "client" | "counterparty" | "employee" | "other";
  participant_client_id: string;
  participant_counterparty_id: string;
  participant_employment_id: string;
  participant_text: string;
  performer_employment_id: string;
};

export function emptyDetails(locationId: string | null): Details {
  return {
    performed_at: "",
    place_kind: "location",
    place_location_id: locationId ?? "",
    place_note: "",
    place_address: "",
    place_lat: "",
    place_lng: "",
    participant_kind: "",
    participant_client_id: "",
    participant_counterparty_id: "",
    participant_employment_id: "",
    participant_text: "",
    performer_employment_id: "",
  };
}

export function detailsOf(inspection: Inspection): Details {
  return {
    performed_at: "",
    place_kind: inspection.place.kind,
    place_location_id: inspection.place.location_id ?? "",
    place_note: inspection.place.note,
    place_address: inspection.place.address,
    place_lat: inspection.place.lat ?? "",
    place_lng: inspection.place.lng ?? "",
    participant_kind: inspection.participant.kind ?? "",
    participant_client_id: inspection.participant.client_id ?? "",
    participant_counterparty_id: inspection.participant.counterparty_id ?? "",
    participant_employment_id: inspection.participant.employment_id ?? "",
    participant_text: inspection.participant.text,
    performer_employment_id: inspection.performer_employment_id,
  };
}

/** The body of the details; the moment only when it was changed, so its seconds stay. */
export function detailsBody(details: Details, zone: string): Record<string, unknown> {
  const other = details.place_kind === "other";
  const kind = details.participant_kind;
  return {
    ...(details.performed_at ? { performed_at: fromLocalInput(details.performed_at, zone) } : {}),
    place_kind: details.place_kind,
    place_location_id: details.place_location_id || null,
    place_note: other ? details.place_note : "",
    place_address: other ? details.place_address : "",
    place_lat: other && details.place_lat ? details.place_lat : null,
    place_lng: other && details.place_lng ? details.place_lng : null,
    participant_kind: kind,
    participant_client_id: kind === "client" ? details.participant_client_id || null : null,
    participant_counterparty_id: kind === "counterparty" ? details.participant_counterparty_id || null : null,
    participant_employment_id: kind === "employee" ? details.participant_employment_id || null : null,
    participant_text: kind === "other" ? details.participant_text : "",
    performer_employment_id: details.performer_employment_id || null,
  };
}

export function employeeName(employee: Employee | undefined): string {
  if (!employee?.user) return "—";
  return `${employee.user.first_name} ${employee.user.last_name}`.trim() || employee.user.email || "—";
}

export function DetailsFields({
  value,
  onChange,
  zone,
  initialMoment,
  clientId,
  clientLabel,
  planned,
}: {
  value: Details;
  onChange: (next: Details) => void;
  zone: string;
  /** The recorded moment shown until it is changed. */
  initialMoment: Date;
  clientId: string | null;
  clientLabel: string;
  planned?: boolean;
}) {
  const { t } = useTranslation();
  const locations = useLocations();
  const employees = useEmployees();
  const counterparties = useContractors(value.participant_kind === "counterparty");
  const [locating, setLocating] = useState<"" | "pending" | "failed">("");
  const set = (patch: Partial<Details>) => onChange({ ...value, ...patch });
  const active = (employees.data?.employees ?? []).filter((e) => e.state === "active" && e.user);
  const now = toLocalInput(new Date(), zone);

  function locate() {
    if (!navigator.geolocation) {
      setLocating("failed");
      return;
    }
    setLocating("pending");
    navigator.geolocation.getCurrentPosition(
      (position) => {
        setLocating("");
        set({
          place_lat: position.coords.latitude.toFixed(6),
          place_lng: position.coords.longitude.toFixed(6),
        });
      },
      () => setLocating("failed"),
      { enableHighAccuracy: true, timeout: 20000 },
    );
  }

  return (
    <div className="stack">
      {planned ? null : (
        <Field label={t("inspections.performed_at")} hint={t("inspections.performed_at_hint", { zone })}>
          <input
            type="datetime-local"
            max={now}
            value={value.performed_at || toLocalInput(initialMoment, zone)}
            onChange={(e) => set({ performed_at: e.target.value })}
          />
        </Field>
      )}
      <Field label={t("inspections.place")}>
        <select value={value.place_kind} onChange={(e) => set({ place_kind: e.target.value as Details["place_kind"] })}>
          <option value="location">{t("inspections.place_location")}</option>
          <option value="other">{t("inspections.place_other")}</option>
        </select>
      </Field>
      {value.place_kind === "location" ? (
        <Field label={t("inspections.location")} hint={t("inspections.location_hint")}>
          <select value={value.place_location_id} onChange={(e) => set({ place_location_id: e.target.value })}>
            <option value="">{t("inspections.vehicle_location")}</option>
            {(locations.data?.locations ?? [])
              .filter((l) => l.state === "active")
              .map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name}
                </option>
              ))}
          </select>
        </Field>
      ) : (
        <div className="stack">
          <p className="muted small">{t("inspections.place_other_hint")}</p>
          <Field label={t("inspections.place_note")}>
            <input value={value.place_note} maxLength={500} onChange={(e) => set({ place_note: e.target.value })} />
          </Field>
          <Field label={t("inspections.place_address")}>
            <input value={value.place_address} maxLength={500} onChange={(e) => set({ place_address: e.target.value })} />
          </Field>
          <div className="row">
            <Field label={t("inspections.place_lat")}>
              <input inputMode="decimal" value={value.place_lat} onChange={(e) => set({ place_lat: e.target.value.trim() })} />
            </Field>
            <Field label={t("inspections.place_lng")}>
              <input inputMode="decimal" value={value.place_lng} onChange={(e) => set({ place_lng: e.target.value.trim() })} />
            </Field>
          </div>
          <div className="actions" style={{ marginTop: 0 }}>
            <button type="button" className="secondary" disabled={locating === "pending"} onClick={locate}>
              {locating === "pending" ? t("inspections.locating") : t("inspections.use_geolocation")}
            </button>
          </div>
          {locating === "failed" ? <p className="alert warn">{t("inspections.geolocation_failed")}</p> : null}
        </div>
      )}
      <Field label={t("inspections.participant")} hint={t("inspections.participant_hint")}>
        <select
          value={value.participant_kind}
          onChange={(e) => {
            const kind = e.target.value as Details["participant_kind"];
            set({ participant_kind: kind, participant_client_id: kind === "client" ? clientId ?? "" : "" });
          }}
        >
          <option value="">{t("inspections.participant_none")}</option>
          {clientId ? <option value="client">{clientLabel}</option> : null}
          <option value="counterparty">{t("inspections.participant_counterparty")}</option>
          <option value="employee">{t("inspections.participant_employee")}</option>
          <option value="other">{t("inspections.participant_other")}</option>
        </select>
      </Field>
      {value.participant_kind === "counterparty" ? (
        <Field label={t("inspections.participant_counterparty")}>
          <select
            value={value.participant_counterparty_id}
            onChange={(e) => set({ participant_counterparty_id: e.target.value })}
          >
            <option value="">—</option>
            {(counterparties.data?.counterparties ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </Field>
      ) : null}
      {value.participant_kind === "employee" ? (
        <Field label={t("inspections.participant_employee")}>
          <select value={value.participant_employment_id} onChange={(e) => set({ participant_employment_id: e.target.value })}>
            <option value="">—</option>
            {active.map((e) => (
              <option key={e.id} value={e.id}>
                {employeeName(e)}
              </option>
            ))}
          </select>
        </Field>
      ) : null}
      {value.participant_kind === "other" ? (
        <Field label={t("inspections.participant_text")}>
          <input value={value.participant_text} maxLength={300} onChange={(e) => set({ participant_text: e.target.value })} />
        </Field>
      ) : null}
      {active.length ? (
        <Field label={t("inspections.performer")} hint={t("inspections.performer_hint")}>
          <select value={value.performer_employment_id} onChange={(e) => set({ performer_employment_id: e.target.value })}>
            <option value="">{t("inspections.performer_me")}</option>
            {active.map((e) => (
              <option key={e.id} value={e.id}>
                {employeeName(e)}
              </option>
            ))}
          </select>
        </Field>
      ) : null}
    </div>
  );
}
