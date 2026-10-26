import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "../../api/queries";
import type { IssueRequirement, Rental } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { ErrorMessage, Field, Loading } from "../../components/ui";
import { useCan, useCompany } from "../../layout/CompanyShell";
import { formatMoment } from "../../time";

/** «In the location» or «other» of the chosen location (§ 14.2.1): an «other» place is described
 * by an address, coordinates (also from the device) or a comment — at least one (decision В1). */
export type PlaceValue = { kind: "location" | "other"; note: string; address: string; lat: string; lng: string };

export const EMPTY_PLACE: PlaceValue = { kind: "location", note: "", address: "", lat: "", lng: "" };

export function placeOf(rental: Rental): PlaceValue {
  return {
    kind: rental.issue_place_kind === "other" ? "other" : "location",
    note: rental.issue_place_note,
    address: rental.issue_place_address,
    lat: rental.issue_place_lat ?? "",
    lng: rental.issue_place_lng ?? "",
  };
}

export function placeBody(place: PlaceValue): Record<string, unknown> {
  if (place.kind === "location") return { place_kind: "location" };
  return {
    place_kind: "other",
    place_note: place.note,
    place_address: place.address,
    place_lat: place.lat || null,
    place_lng: place.lng || null,
  };
}

export function placeText(rental: Rental): string {
  if (rental.issue_place_kind !== "other") return "";
  const parts = [rental.issue_place_address, rental.issue_place_note];
  if (rental.issue_place_lat) parts.push(`${rental.issue_place_lat}, ${rental.issue_place_lng}`);
  return parts.filter(Boolean).join(" · ");
}

export function PlaceFields({ value, onChange }: { value: PlaceValue; onChange: (next: PlaceValue) => void }) {
  const { t } = useTranslation();
  const [locating, setLocating] = useState(false);
  const [failed, setFailed] = useState(false);
  const set = (patch: Partial<PlaceValue>) => onChange({ ...value, ...patch });

  function locate() {
    setFailed(false);
    if (!("geolocation" in navigator)) {
      setFailed(true);
      return;
    }
    setLocating(true);
    navigator.geolocation.getCurrentPosition(
      (position) => {
        setLocating(false);
        set({ lat: position.coords.latitude.toFixed(6), lng: position.coords.longitude.toFixed(6) });
      },
      () => {
        setLocating(false);
        setFailed(true);
      },
      { enableHighAccuracy: true, timeout: 15000 },
    );
  }

  return (
    <>
      <div className="row">
        <Field label={t("rentals.place")}>
          <select value={value.kind} onChange={(e) => set({ kind: e.target.value as PlaceValue["kind"] })}>
            <option value="location">{t("rentals.place_location")}</option>
            <option value="other">{t("rentals.place_other")}</option>
          </select>
        </Field>
        {value.kind === "other" ? (
          <Field label={t("issue_rules.address")}>
            <input value={value.address} onChange={(e) => set({ address: e.target.value })} />
          </Field>
        ) : null}
      </div>
      {value.kind === "other" ? (
        <>
          <div className="row">
            <Field label={t("issue_rules.lat")}>
              <input value={value.lat} inputMode="decimal" onChange={(e) => set({ lat: e.target.value })} />
            </Field>
            <Field label={t("issue_rules.lng")}>
              <input value={value.lng} inputMode="decimal" onChange={(e) => set({ lng: e.target.value })} />
            </Field>
            <Field label={t("rentals.place_note")}>
              <input value={value.note} onChange={(e) => set({ note: e.target.value })} />
            </Field>
          </div>
          <div className="actions" style={{ marginTop: 0 }}>
            <button type="button" className="secondary" disabled={locating} onClick={locate}>
              {t("issue_rules.my_location")}
            </button>
          </div>
          {failed ? <p className="muted small">{t("issue_rules.location_failed")}</p> : null}
          <p className="muted small">{t("issue_rules.place_hint")}</p>
        </>
      ) : null}
    </>
  );
}

export function requirementText(t: (key: string, options?: Record<string, unknown>) => string, item: IssueRequirement) {
  const amounts = Object.entries(item.amounts ?? {})
    .map(([currency, amount]) => `${amount} ${currency}`)
    .join(", ");
  return t(`issue_rules.requirement_${item.code}`, { amounts });
}

export type ExceptionValue = { mode: "" | "advance" | "after_fact"; reason: string };

/** Strict requirements left unmet (§ 14.3.7): the authorized employee may permit the issue now;
 * if the vehicle has already been handed over, the issuing employee records the fact and it waits
 * for a decision. Neither meets the requirement. */
export function ExceptionChoice({
  unmet,
  canAuthorize,
  value,
  onChange,
}: {
  unmet: IssueRequirement[];
  canAuthorize: boolean;
  value: ExceptionValue;
  onChange: (next: ExceptionValue) => void;
}) {
  const { t } = useTranslation();
  if (unmet.length === 0) return null;
  return (
    <div className="alert warn stack">
      <strong>{t("issue_rules.unmet_title")}</strong>
      <ul>
        {unmet.map((item) => (
          <li key={item.code}>{requirementText(t, item)}</li>
        ))}
      </ul>
      {canAuthorize ? (
        <label className="small" style={{ display: "block" }}>
          <input
            type="radio"
            checked={value.mode === "advance"}
            onChange={() => onChange({ ...value, mode: "advance" })}
          />{" "}
          {t("issue_rules.mode_advance")}
        </label>
      ) : null}
      <label className="small" style={{ display: "block" }}>
        <input
          type="radio"
          checked={value.mode === "after_fact"}
          onChange={() => onChange({ ...value, mode: "after_fact" })}
        />{" "}
        {t("issue_rules.mode_after_fact")}
      </label>
      {value.mode ? (
        <Field label={t("issue_rules.reason")}>
          <input value={value.reason} onChange={(e) => onChange({ ...value, reason: e.target.value })} />
        </Field>
      ) : (
        <p className="small">{t(canAuthorize ? "issue_rules.choose" : "issue_rules.no_right")}</p>
      )}
    </div>
  );
}

/** The exception of an issued rental, with the decision on one recorded after the transfer. */
export function IssueExceptionView({ rental, onDone }: { rental: Rental; onDone: (next?: Rental) => Promise<void> }) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const [reason, setReason] = useState("");
  const decide = useCommand<Record<string, unknown>, { rental: Rental }>(
    `/c/${company.public_id}/rentals/${rental.id}/issue-exception/decide`,
  );
  const item = rental.issue_exception;
  if (!item) return null;
  const when = (value: string | null) => formatMoment(value, rental.calc_timezone, i18n.language);

  async function run(decision: "confirm" | "reject") {
    const result = await decide.run({ decision, reason, expected_version: rental.version });
    if (result) await onDone(result.rental);
  }

  return (
    <div className="stack">
      <div className={item.state === "pending" || item.state === "rejected" ? "alert warn" : "alert"}>
        <strong>{t(`issue_rules.state_${item.state}`)}</strong>
        <div>
          {t("issue_rules.covers")}: {item.requirements.map((code) => t(`issue_rules.code_${code}`)).join(", ")}
        </div>
        <div>
          {t("issue_rules.reason")}: {item.reason} · {when(item.created_at)}
        </div>
        {item.decision_reason ? (
          <div>
            {t("issue_rules.decision")}: {item.decision_reason} · {when(item.decided_at)}
          </div>
        ) : null}
      </div>
      {item.state === "pending" && can("rental.exception.decide") ? (
        <>
          <Field label={t("issue_rules.decision_reason")}>
            <input value={reason} onChange={(e) => setReason(e.target.value)} />
          </Field>
          <ErrorMessage error={decide.error} />
          <div className="actions">
            <button type="button" disabled={decide.pending || !reason.trim()} onClick={() => void run("confirm")}>
              {t("issue_rules.confirm")}
            </button>
            <button
              type="button"
              className="danger"
              disabled={decide.pending || !reason.trim()}
              onClick={() => void run("reject")}
            >
              {t("issue_rules.reject")}
            </button>
          </div>
        </>
      ) : null}
    </div>
  );
}

const REQUIREMENTS = ["payment", "debt", "documents"] as const;

/** Advance permission on a booking (§ 14.3.7; decision В6): for one issue, with a reason. */
export function GrantException({ rental, onDone }: { rental: Rental; onDone: (next?: Rental) => Promise<void> }) {
  const { t } = useTranslation();
  const company = useCompany();
  const current = rental.issue_exception?.state === "granted" ? rental.issue_exception : null;
  const [chosen, setChosen] = useState<string[]>(current?.requirements ?? []);
  const [reason, setReason] = useState(current?.reason ?? "");
  const base = `/c/${company.public_id}/bookings/${rental.id}/issue-exception`;
  const grant = useCommand<Record<string, unknown>, { rental: Rental }>(base);
  const revoke = useCommand<Record<string, unknown>, { rental: Rental }>(`${base}/revoke`);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await grant.run({ requirements: chosen, reason, expected_version: rental.version });
    if (result) await onDone(result.rental);
  }

  return (
    <form onSubmit={submit}>
      <p className="muted small">{t("issue_rules.grant_hint")}</p>
      {REQUIREMENTS.map((code) => (
        <label key={code} className="small" style={{ display: "block" }}>
          <input
            type="checkbox"
            checked={chosen.includes(code)}
            onChange={() => setChosen(chosen.includes(code) ? chosen.filter((c) => c !== code) : [...chosen, code])}
          />{" "}
          {t(`issue_rules.code_${code}`)}
        </label>
      ))}
      <Field label={t("issue_rules.reason")}>
        <input value={reason} onChange={(e) => setReason(e.target.value)} />
      </Field>
      <ErrorMessage error={grant.error ?? revoke.error} />
      <div className="actions">
        <button type="submit" disabled={grant.pending || chosen.length === 0 || !reason.trim()}>
          {t("issue_rules.grant")}
        </button>
        {current ? (
          <button
            type="button"
            className="secondary"
            disabled={revoke.pending}
            onClick={() =>
              void revoke.run({ expected_version: rental.version }).then((r) => (r ? onDone(r.rental) : undefined))
            }
          >
            {t("issue_rules.revoke")}
          </button>
        ) : null}
      </div>
    </form>
  );
}

type IssuePolicy = {
  own: boolean;
  debt: string;
  payment: string;
  documents: string;
  inspection: string;
  effective: { debt: string; payment: string; documents: string; inspection: string; source: string };
};

const POLICY_CHOICES = {
  debt: ["allow", "warn", "decide"],
  payment: ["warn", "require"],
  documents: ["none", "require"],
  inspection: ["not_required", "required"],
} as const;

/** Policies of the issue for the company or a location (§ 14.3.2, § 14.3.5–14.3.6; В2–В5). */
export function IssuePolicyForm({ locationId }: { locationId: string | null }) {
  const company = useCompany();
  const query = locationId ? `?location_id=${locationId}` : "";
  const key = ["issue-policy", company.public_id, locationId ?? ""];
  const policy = useApi<{ policy: IssuePolicy }>(key, `/c/${company.public_id}/issue-policy${query}`);
  if (!policy.data) return <Loading />;
  return <IssuePolicyFields policy={policy.data.policy} path={`/c/${company.public_id}/issue-policy${query}`} />;
}

function IssuePolicyFields({ policy, path }: { policy: IssuePolicy; path: string }) {
  const { t } = useTranslation();
  const can = useCan();
  const queryClient = useQueryClient();
  const source = policy.own ? policy : policy.effective;
  const [values, setValues] = useState({
    debt: source.debt,
    payment: source.payment,
    documents: source.documents,
    inspection: source.inspection,
  });
  const save = useCommand<Record<string, unknown>, { policy: IssuePolicy }>(path, "PUT");
  const editable = can("company.settings");

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (await save.run(values)) await queryClient.invalidateQueries({ queryKey: ["issue-policy"] });
  }

  return (
    <form onSubmit={submit}>
      <h3>{t("issue_rules.policy_title")}</h3>
      <p className="muted small">
        {t("issue_rules.policy_source", { source: t(`bookings.policy_source_${policy.effective.source}`) })}
      </p>
      <div className="row">
        {(Object.keys(POLICY_CHOICES) as (keyof typeof POLICY_CHOICES)[]).map((name) => (
          <Field key={name} label={t(`issue_rules.policy_${name}`)}>
            <select
              value={values[name]}
              disabled={!editable}
              onChange={(e) => setValues({ ...values, [name]: e.target.value })}
            >
              {POLICY_CHOICES[name].map((choice) => (
                <option key={choice} value={choice}>
                  {t(`issue_rules.${name}_${choice}`)}
                </option>
              ))}
            </select>
          </Field>
        ))}
      </div>
      <p className="muted small">{t("issue_rules.policy_hint")}</p>
      <ErrorMessage error={save.error} />
      {editable ? (
        <div className="actions">
          <button type="submit" disabled={save.pending}>
            {t("common.save")}
          </button>
        </div>
      ) : null}
    </form>
  );
}
