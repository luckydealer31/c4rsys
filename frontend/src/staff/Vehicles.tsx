import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router";

import type { ApiError } from "../api/client";
import { useApi } from "../api/queries";
import { useCommand } from "../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../components/ui";
import { useStaffCan, useWhen } from "./common";

type ConflictSummary = {
  id: string;
  number: number;
  kind: string;
  value: string;
  country: string;
  open: boolean;
  review: string;
  detected_at: string;
  closed_at: string | null;
  entities: number;
};
type VehicleSummary = {
  vehicle_id: string;
  entity: string;
  make: string;
  model: string;
  year: number | null;
  vin: string;
  plate: string;
  plate_country: string;
  plate_region: string;
  body_number: string;
  chassis_number: string;
  frame_number: string;
  confirmed_fields: string[];
  owners: number;
};
type ConflictDetail = ConflictSummary & {
  parties: {
    id: string;
    vehicle: VehicleSummary;
    company: { public_id: string; name: string } | null;
    card: { id: string; name: string; relation: string; state: string; created_at: string } | null;
    original_value: string;
    joined_at: string;
    left_at: string | null;
  }[];
  decisions: {
    id: string;
    outcome: string;
    reason: string;
    evidence: string;
    primary: string | null;
    decided_at: string;
    by_system: boolean;
  }[];
  operational_companies: number;
  operational_disputes: OperationalDispute[];
};

/** В-12.3: a dispute of ownership or management after the link of one vehicle in the own or
 * managed fleets of two companies. */
export type OperationalDispute = {
  id: string;
  number: number;
  conflict_id: string;
  vehicle: string;
  open: boolean;
  opened_at: string;
  closed_at: string | null;
  reason: string;
  evidence: string;
  current_owner: { type: "user" | "company"; public_id: string; name: string } | null;
  parties: {
    company: { public_id: string; name: string };
    card: { id: string; name: string; relation_at_link: string; relation: string; state: string };
  }[];
};

/** В-12.3: the parties of the dispute and the decision of the verification. */
export function OperationalDisputeCard({ dispute: d }: { dispute: OperationalDispute }) {
  const { t } = useTranslation();
  const when = useWhen();
  const client = useQueryClient();
  const [reason, setReason] = useState("");
  const [evidence, setEvidence] = useState("");
  const [ticket, setTicket] = useState("");
  const close = useCommand<Record<string, unknown>, { dispute: OperationalDispute }>(
    `/staff/vehicle-disputes/${d.id}/close`,
  );
  return (
    <Card title={t("staff.operational_dispute_title", { number: d.number })}>
      <p>
        <span className={d.open ? "badge warn" : "badge"}>
          {d.open ? t("staff.operational_dispute_open") : t("staff.operational_dispute_closed", { at: when(d.closed_at) })}
        </span>{" "}
        <Link to={`/vehicles/${d.vehicle}`}>{d.vehicle}</Link> ·{" "}
        <Link to={`/vehicle-conflicts/${d.conflict_id}`}>№ {d.number}</Link> ·{" "}
        <span className="muted small">{when(d.opened_at)}</span>
      </p>
      <p className="small">
        {t("staff.operational_dispute_owner")}:{" "}
        {d.current_owner ? `${d.current_owner.name || "—"} (${d.current_owner.public_id})` : t("staff.none")}
      </p>
      <ul className="plain">
        {d.parties.map((p) => (
          <li key={p.card.id} className="small">
            {p.company.name} ({p.company.public_id}) · «{p.card.name}» ·{" "}
            {t(`staff.relation_${p.card.relation_at_link}`, { defaultValue: p.card.relation_at_link })}
            {p.card.relation !== p.card.relation_at_link || p.card.state !== "active"
              ? ` → ${t(`staff.relation_${p.card.relation}`, { defaultValue: p.card.relation })} · ${p.card.state}`
              : ""}
          </li>
        ))}
      </ul>
      {d.open ? (
        <form
          className="stack"
          onSubmit={async (event) => {
            event.preventDefault();
            const done = await close.run({
              reason,
              evidence,
              ticket_number: ticket ? Number(ticket) : undefined,
            });
            if (done) {
              setReason("");
              await client.invalidateQueries({ queryKey: ["staff"] });
            }
          }}
        >
          <p className="muted small">{t("staff.operational_dispute_hint")}</p>
          <Field label={t("staff.verify_reason")}>
            <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={2} />
          </Field>
          <Field label={t("staff.conflict_evidence")}>
            <textarea value={evidence} onChange={(e) => setEvidence(e.target.value)} rows={2} />
          </Field>
          <Field label={t("staff.ticket_number")}>
            <input value={ticket} inputMode="numeric" onChange={(e) => setTicket(e.target.value.replace(/\D/g, ""))} />
          </Field>
          <ErrorMessage error={close.error} />
          <div className="actions">
            <button type="submit" disabled={close.pending || !reason.trim()}>
              {t("staff.operational_dispute_close")}
            </button>
          </div>
        </form>
      ) : (
        <p className="small">
          {d.reason}
          {d.evidence ? ` · ${d.evidence}` : ""}
        </p>
      )}
    </Card>
  );
}

const OUTCOMES = ["same_vehicle", "exception", "error", "insufficient"] as const;
const CHOICE_LABELS: Record<string, string> = {
  vehicle_type: "vehicles.type_",
  body_type: "vehicles.body.",
  fuel_type: "vehicles.fuel.",
  transmission: "vehicles.gearbox.",
  drive: "vehicles.drives.",
};

/** § 8.6.1–8.6.2, § 24.1.5: the conflicts of VIN and other identifiers for the verification. */
export function ConflictsPage() {
  const { t } = useTranslation();
  const when = useWhen();
  const [state, setState] = useState<"open" | "closed">("open");
  const list = useApi<{ conflicts: ConflictSummary[] }>(
    ["staff", "vehicle-conflicts", state],
    `/staff/vehicle-conflicts?state=${state}`,
  );
  return (
    <div className="page">
      <h1>{t("staff.nav_conflicts")}</h1>
      <p className="muted small">{t("staff.conflicts_hint")}</p>
      <div className="tabs" role="tablist">
        {(["open", "closed"] as const).map((s) => (
          <button key={s} type="button" role="tab" aria-selected={state === s} className={state === s ? "active" : ""} onClick={() => setState(s)}>
            {t(`staff.conflicts_${s}`)}
          </button>
        ))}
      </div>
      {list.isLoading ? <Loading /> : null}
      <ErrorMessage error={list.error as ApiError | null} />
      <Card>
        {list.data && !list.data.conflicts.length ? <p className="muted">{t("staff.none")}</p> : null}
        <table>
          <tbody>
            {(list.data?.conflicts ?? []).map((c) => (
              <tr key={c.id}>
                <td>
                  <Link to={`/vehicle-conflicts/${c.id}`}>№ {c.number}</Link>
                </td>
                <td>
                  {t(`staff.identifier_${c.kind}`)} · {c.value}
                  {c.country ? ` (${c.country})` : ""}
                </td>
                <td>{t(`staff.conflict_review_${c.review}`)}</td>
                <td className="muted small">{when(c.detected_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

export function ConflictPage() {
  const { t } = useTranslation();
  const when = useWhen();
  const { id = "" } = useParams();
  const client = useQueryClient();
  const found = useApi<{ conflict: ConflictDetail }>(["staff", "vehicle-conflict", id], `/staff/vehicle-conflicts/${id}`);
  const [outcome, setOutcome] = useState<(typeof OUTCOMES)[number]>("insufficient");
  const [primary, setPrimary] = useState("");
  const [reason, setReason] = useState("");
  const [evidence, setEvidence] = useState("");
  const [ticket, setTicket] = useState("");
  const decide = useCommand<Record<string, unknown>, { conflict: ConflictDetail }>(`/staff/vehicle-conflicts/${id}/decide`);
  if (found.isLoading) return <div className="page"><Loading /></div>;
  const c = found.data?.conflict;
  if (!c) return <div className="page"><ErrorMessage error={found.error as ApiError | null} /></div>;
  const entities = Array.from(new Set(c.parties.filter((p) => !p.left_at).map((p) => p.vehicle.entity)));
  const decidable = c.open || c.review === "exception";
  return (
    <div className="page">
      <p>
        <Link to="/vehicle-conflicts">← {t("staff.nav_conflicts")}</Link>
      </p>
      <h1>
        № {c.number} · {t(`staff.identifier_${c.kind}`)} {c.value}
      </h1>
      <p>
        <span className={c.open ? "badge warn" : "badge"}>{t(`staff.conflict_review_${c.review}`)}</span>{" "}
        <span className="muted small">{when(c.detected_at)}</span>
      </p>
      {c.operational_companies > 1 && c.open ? (
        <div className="alert warn">{t("staff.conflict_operational_twice")}</div>
      ) : null}
      {(c.operational_disputes ?? []).map((d) => (
        <OperationalDisputeCard key={d.id} dispute={d} />
      ))}
      <Card title={t("staff.conflict_parties")}>
        <table>
          <thead>
            <tr>
              <th>{t("staff.conflict_vehicle_column")}</th>
              <th>{t("staff.conflict_company")}</th>
              <th>{t("staff.conflict_identifiers")}</th>
              <th>{t("staff.conflict_original")}</th>
            </tr>
          </thead>
          <tbody>
            {c.parties.map((p) => (
              <tr key={p.id} className={p.left_at ? "muted" : ""}>
                <td>
                  <Link to={`/vehicles/${p.vehicle.vehicle_id}`}>{p.vehicle.vehicle_id}</Link>
                  {p.vehicle.entity !== p.vehicle.vehicle_id ? <div className="small">→ {p.vehicle.entity}</div> : null}
                  <div className="small">
                    {[p.vehicle.make, p.vehicle.model, p.vehicle.year].filter(Boolean).join(" ")}
                  </div>
                  {p.vehicle.owners ? <div className="small">{t("staff.conflict_has_owners")}</div> : null}
                </td>
                <td>
                  {p.company ? `${p.company.name} (${p.company.public_id})` : "—"}
                  {p.card ? (
                    <div className="small">
                      «{p.card.name}» · {t(`staff.relation_${p.card.relation}`, { defaultValue: p.card.relation })} ·{" "}
                      {p.card.state}
                    </div>
                  ) : null}
                </td>
                <td className="small">
                  VIN {p.vehicle.vin || "—"}
                  <br />
                  {t("staff.identifier_plate")} {[p.vehicle.plate, p.vehicle.plate_country, p.vehicle.plate_region].filter(Boolean).join(" ") || "—"}
                  {p.vehicle.body_number ? <><br />{t("staff.identifier_body_number")} {p.vehicle.body_number}</> : null}
                  {p.vehicle.chassis_number ? <><br />{t("staff.identifier_chassis_number")} {p.vehicle.chassis_number}</> : null}
                  {p.vehicle.frame_number ? <><br />{t("staff.identifier_frame_number")} {p.vehicle.frame_number}</> : null}
                </td>
                <td>
                  «{p.original_value}»
                  {p.left_at ? <div className="small">{t("staff.conflict_party_left", { at: when(p.left_at) })}</div> : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <Card title={t("staff.conflict_decisions")}>
        {c.decisions.length ? (
          <ul className="plain">
            {c.decisions.map((d) => (
              <li key={d.id} className="small">
                {when(d.decided_at)} · <strong>{t(`staff.conflict_outcome_${d.outcome}`)}</strong>
                {d.primary ? ` · ${d.primary}` : ""} · {d.by_system ? t("staff.conflict_by_system") : d.reason}
                {d.evidence ? ` · ${d.evidence}` : ""}
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">{t("staff.none")}</p>
        )}
      </Card>
      {decidable ? (
        <Card title={c.open ? t("staff.conflict_decide") : t("staff.conflict_revise")}>
          <form
            className="stack"
            onSubmit={async (e) => {
              e.preventDefault();
              const done = await decide.run({
                outcome,
                reason,
                evidence,
                ...(outcome === "same_vehicle" ? { primary } : {}),
                ...(ticket ? { ticket_number: Number(ticket) } : {}),
              });
              if (done) {
                client.setQueryData(["staff", "vehicle-conflict", id], done);
                void client.invalidateQueries({ queryKey: ["staff", "vehicle-conflicts"] });
                setReason("");
                setEvidence("");
              }
            }}
          >
            <div className="row">
              <Field label={t("staff.conflict_outcome")}>
                <select value={outcome} onChange={(e) => setOutcome(e.target.value as (typeof OUTCOMES)[number])}>
                  {OUTCOMES.filter((o) => c.open || o !== "exception").map((o) => (
                    <option key={o} value={o}>
                      {t(`staff.conflict_outcome_${o}`)}
                    </option>
                  ))}
                </select>
              </Field>
              {outcome === "same_vehicle" ? (
                <Field label={t("staff.conflict_primary")} hint={t("staff.conflict_primary_hint")}>
                  <select value={primary} onChange={(e) => setPrimary(e.target.value)}>
                    <option value="">—</option>
                    {entities.map((v) => (
                      <option key={v} value={v}>
                        {v}
                      </option>
                    ))}
                  </select>
                </Field>
              ) : null}
              <Field label={t("staff.ticket_number")}>
                <input inputMode="numeric" value={ticket} onChange={(e) => setTicket(e.target.value.replace(/\D/g, ""))} />
              </Field>
            </div>
            <p className="muted small">{t(`staff.conflict_outcome_hint_${outcome}`)}</p>
            <Field label={t("staff.verify_reason")}>
              <textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />
            </Field>
            <Field label={t("staff.conflict_evidence")}>
              <textarea rows={2} value={evidence} onChange={(e) => setEvidence(e.target.value)} />
            </Field>
            <ErrorMessage error={decide.error} />
            <div className="actions">
              <button type="submit" disabled={decide.pending || !reason.trim() || (outcome === "same_vehicle" && !primary)}>
                {t("staff.verify_decide")}
              </button>
            </div>
          </form>
        </Card>
      ) : null}
    </div>
  );
}

type Shown = { value: unknown; display: string };
type GlobalVehicle = {
  vehicle_id: string;
  entity: string;
  merged_at: string | null;
  values: Record<string, Shown>;
  factory_fields: string[];
  confirmed_fields: string[];
  events: { field: string; action: string; before: Shown; after: Shown; reason: string; decided_at: string }[];
  ownerships: {
    id: string;
    owner: { type: string; public_id: string; name: string } | null;
    started_at: string;
    ended_at: string | null;
    started_approx: boolean;
    ended_approx: boolean;
  }[];
  plates: { number: string; country: string; region: string; valid_from: string; valid_to: string | null }[];
  cards: { company: string; relation: string; state: string }[];
};
type Proposal = {
  id: string;
  field: string;
  current: Shown;
  proposed: Shown;
  reason: string;
  created_at: string;
  vehicle_id: string;
  company: { public_id: string; name: string };
  card: { name: string; relation: string };
};

/** § 8.2.1, § 8.3.16: the global vehicle — search, confirmed characteristics and the decisions on
 * proposals of companies. */
export function VehiclesPage() {
  const { t } = useTranslation();
  const can = useStaffCan();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [shown, setShown] = useState("");
  const found = useApi<{ vehicles: { vehicle_id: string; vin: string; merged: boolean }[] }>(
    ["staff", "vehicles", shown],
    `/staff/vehicles?q=${encodeURIComponent(shown)}`,
    Boolean(shown),
  );
  return (
    <div className="page">
      <h1>{t("staff.nav_vehicles")}</h1>
      <Card>
        <form
          className="row"
          onSubmit={(e) => {
            e.preventDefault();
            setShown(query.trim());
          }}
        >
          <input value={query} placeholder={t("staff.vehicles_search")} aria-label={t("staff.vehicles_search")} onChange={(e) => setQuery(e.target.value)} />
          <button type="submit" className="secondary" disabled={!query.trim()}>
            {t("staff.verify_find")}
          </button>
        </form>
        <ErrorMessage error={found.error as ApiError | null} />
        {found.data ? (
          found.data.vehicles.length ? (
            <ul className="plain">
              {found.data.vehicles.map((v) => (
                <li key={v.vehicle_id}>
                  <button type="button" className="link" onClick={() => navigate(`/vehicles/${v.vehicle_id}`)}>
                    {v.vehicle_id}
                  </button>{" "}
                  {v.vin} {v.merged ? `· ${t("staff.vehicle_archive_reference")}` : ""}
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">{t("staff.none")}</p>
          )
        ) : null}
      </Card>
      {can("vehicles.characteristics") ? <ProposalsSection /> : null}
    </div>
  );
}

function ProposalsSection() {
  const { t } = useTranslation();
  const when = useWhen();
  const list = useApi<{ proposals: Proposal[] }>(["staff", "vehicle-proposals"], "/staff/vehicle-proposals");
  return (
    <Card title={t("staff.proposals_title")}>
      <p className="muted small">{t("staff.proposals_hint")}</p>
      {list.data && !list.data.proposals.length ? <p className="muted">{t("staff.none")}</p> : null}
      {(list.data?.proposals ?? []).map((p) => (
        <div key={p.id} className="list-link stack">
          <div>
            <Link to={`/vehicles/${p.vehicle_id}`}>{p.vehicle_id}</Link> · {p.company.name} ({p.company.public_id}) · «{p.card.name}»
          </div>
          <div>
            {t(`vehicles.${p.field}`)}: {p.current.display || "—"} → <strong>{p.proposed.display || "—"}</strong>
          </div>
          <div className="small">
            {p.reason} · <span className="muted">{when(p.created_at)}</span>
          </div>
          <ProposalDecision id={p.id} />
        </div>
      ))}
    </Card>
  );
}

function ProposalDecision({ id }: { id: string }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [reason, setReason] = useState("");
  const decide = useCommand<Record<string, unknown>, unknown>(`/staff/vehicle-proposals/${id}/decide`);
  const run = async (accept: boolean) => {
    if (await decide.run({ accept, reason })) void client.invalidateQueries({ queryKey: ["staff"] });
  };
  return (
    <div className="stack">
      <Field label={t("staff.verify_reason")}>
        <input value={reason} onChange={(e) => setReason(e.target.value)} />
      </Field>
      <ErrorMessage error={decide.error} />
      <div className="actions">
        <button type="button" disabled={!reason.trim() || decide.pending} onClick={() => void run(true)}>
          {t("staff.proposal_accept")}
        </button>
        <button type="button" className="danger" disabled={!reason.trim() || decide.pending} onClick={() => void run(false)}>
          {t("staff.proposal_reject")}
        </button>
      </div>
    </div>
  );
}

export function GlobalVehiclePage() {
  const { t } = useTranslation();
  const when = useWhen();
  const can = useStaffCan();
  const { vhl = "" } = useParams();
  const client = useQueryClient();
  const found = useApi<{ vehicle: GlobalVehicle }>(["staff", "vehicle", vhl], `/staff/vehicles/${vhl}`);
  const [chosen, setChosen] = useState<Record<string, string | null>>({});
  const [reason, setReason] = useState("");
  const [ticket, setTicket] = useState("");
  const confirm = useCommand<Record<string, unknown>, { vehicle: GlobalVehicle }>(`/staff/vehicles/${vhl}/confirm`);
  if (found.isLoading) return <div className="page"><Loading /></div>;
  const v = found.data?.vehicle;
  if (!v) return <div className="page"><ErrorMessage error={found.error as ApiError | null} /></div>;
  const refresh = (data: { vehicle: GlobalVehicle }) => client.setQueryData(["staff", "vehicle", vhl], data);
  const shown = (field: string, value: Shown | undefined) => {
    if (!value?.display) return "—";
    const prefix = CHOICE_LABELS[field];
    return prefix ? t(`${prefix}${value.display}`) : value.display;
  };
  return (
    <div className="page">
      <p>
        <Link to="/vehicles">← {t("staff.nav_vehicles")}</Link>
      </p>
      <h1>{v.vehicle_id}</h1>
      {v.entity !== v.vehicle_id ? (
        <div className="alert">
          {t("staff.vehicle_linked_to")} <Link to={`/vehicles/${v.entity}`}>{v.entity}</Link>
        </div>
      ) : null}
      <Card title={t("staff.vehicle_characteristics")}>
        <p className="muted small">{t("staff.vehicle_confirm_hint")}</p>
        <table>
          <tbody>
            {v.factory_fields.map((f) => (
              <tr key={f}>
                <td>{t(`vehicles.${f}`)}</td>
                <td>
                  {shown(f, v.values[f])}
                  {v.confirmed_fields.includes(f) ? <span className="badge ok" style={{ marginLeft: 6 }}>{t("vehicles.confirmed_mark")}</span> : null}
                </td>
                {can("vehicles.characteristics") && v.entity === v.vehicle_id ? (
                  <td>
                    <label className="small">
                      <input
                        type="checkbox"
                        checked={f in chosen}
                        onChange={(e) => {
                          setChosen(
                            e.target.checked
                              ? { ...chosen, [f]: null }
                              : Object.fromEntries(Object.entries(chosen).filter(([key]) => key !== f)),
                          );
                        }}
                      />{" "}
                      {t("staff.vehicle_confirm_field")}
                    </label>
                    {f in chosen && f !== "make" && f !== "model" ? (
                      <input
                        value={chosen[f] ?? ""}
                        placeholder={t("staff.vehicle_new_value")}
                        aria-label={t("staff.vehicle_new_value")}
                        onChange={(e) => setChosen({ ...chosen, [f]: e.target.value })}
                      />
                    ) : null}
                  </td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </table>
        {Object.keys(chosen).length ? (
          <form
            className="stack"
            onSubmit={async (e) => {
              e.preventDefault();
              const numeric = new Set(["year", "power_hp", "seats", "doors"]);
              const items = Object.entries(chosen).map(([field, value]) =>
                value === null || value === ""
                  ? { field }
                  : { field, [field]: numeric.has(field) ? Number(value) : value },
              );
              const done = await confirm.run({ items, reason, ...(ticket ? { ticket_number: Number(ticket) } : {}) });
              if (done) {
                refresh(done);
                setChosen({});
                setReason("");
              }
            }}
          >
            <div className="row">
              <Field label={t("staff.verify_reason")}>
                <input value={reason} onChange={(e) => setReason(e.target.value)} />
              </Field>
              <Field label={t("staff.ticket_number")}>
                <input inputMode="numeric" value={ticket} onChange={(e) => setTicket(e.target.value.replace(/\D/g, ""))} />
              </Field>
            </div>
            <ErrorMessage error={confirm.error} />
            <div className="actions">
              <button type="submit" disabled={!reason.trim() || confirm.pending}>
                {t("staff.vehicle_confirm_submit")}
              </button>
            </div>
          </form>
        ) : null}
      </Card>
      <Card title={t("staff.vehicle_history")}>
        {v.events.length ? (
          <ul className="plain">
            {v.events.map((e, i) => (
              <li key={i} className="small">
                {when(e.decided_at)} · {t(`vehicles.${e.field}`)} · {t(`staff.vehicle_event_${e.action}`)} · {e.before.display || "—"} →{" "}
                {e.after.display || "—"} · {e.reason}
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">{t("staff.none")}</p>
        )}
      </Card>
      <Card title={t("staff.vehicle_ownerships")}>
        {v.ownerships.length ? (
          v.ownerships.map((o) => <OwnershipRow key={o.id} vhl={v.vehicle_id} ownership={o} onDone={refresh} />)
        ) : (
          <p className="muted">{t("staff.none")}</p>
        )}
      </Card>
      <Card title={t("staff.vehicle_plates")}>
        <ul className="plain">
          {v.plates.map((p, i) => (
            <li key={i} className="small">
              {[p.number, p.country, p.region].filter(Boolean).join(" ")} · {when(p.valid_from)} — {p.valid_to ? when(p.valid_to) : "…"}
            </li>
          ))}
        </ul>
        <h3>{t("staff.vehicle_cards")}</h3>
        <ul className="plain">
          {v.cards.map((c, i) => (
            <li key={i} className="small">
              {c.company} · {t(`staff.relation_${c.relation}`, { defaultValue: c.relation })} · {c.state}
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}

function OwnershipRow({
  vhl,
  ownership: o,
  onDone,
}: {
  vhl: string;
  ownership: GlobalVehicle["ownerships"][number];
  onDone: (data: { vehicle: GlobalVehicle }) => void;
}) {
  const { t } = useTranslation();
  const when = useWhen();
  const can = useStaffCan();
  const [editing, setEditing] = useState(false);
  const local = (value: string | null) => (value ? value.slice(0, 16) : "");
  const [start, setStart] = useState(local(o.started_at));
  const [end, setEnd] = useState(local(o.ended_at));
  const [startApprox, setStartApprox] = useState(o.started_approx);
  const [endApprox, setEndApprox] = useState(o.ended_approx);
  const [initiator, setInitiator] = useState("");
  const [reason, setReason] = useState("");
  const [evidence, setEvidence] = useState("");
  const correct = useCommand<Record<string, unknown>, { vehicle: GlobalVehicle }>(`/staff/vehicles/${vhl}/ownerships/${o.id}/correct`);
  return (
    <div className="stack" style={{ marginBottom: 8 }}>
      <span>
        {o.owner ? `${o.owner.name || "—"} (${o.owner.public_id})` : "—"} · {when(o.started_at)}
        {o.started_approx ? ` ${t("staff.approx")}` : ""} — {o.ended_at ? when(o.ended_at) : "…"}
        {o.ended_approx ? ` ${t("staff.approx")}` : ""}
      </span>
      {can("verification.resolve") ? (
        editing ? (
          <form
            className="stack"
            onSubmit={async (e) => {
              e.preventDefault();
              const done = await correct.run({
                started_at: new Date(start).toISOString(),
                ended_at: end ? new Date(end).toISOString() : null,
                started_approx: startApprox,
                ended_approx: endApprox,
                initiator,
                reason,
                evidence,
              });
              if (done) {
                onDone(done);
                setEditing(false);
              }
            }}
          >
            <div className="row">
              <Field label={t("staff.ownership_start")}>
                <input type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} />
              </Field>
              <label className="small">
                <input type="checkbox" checked={startApprox} onChange={(e) => setStartApprox(e.target.checked)} /> {t("staff.approx")}
              </label>
              <Field label={t("staff.ownership_end")}>
                <input type="datetime-local" value={end} onChange={(e) => setEnd(e.target.value)} />
              </Field>
              <label className="small">
                <input type="checkbox" checked={endApprox} onChange={(e) => setEndApprox(e.target.checked)} /> {t("staff.approx")}
              </label>
            </div>
            <Field label={t("staff.ownership_initiator")}>
              <input value={initiator} onChange={(e) => setInitiator(e.target.value)} />
            </Field>
            <Field label={t("staff.verify_reason")}>
              <input value={reason} onChange={(e) => setReason(e.target.value)} />
            </Field>
            <Field label={t("staff.conflict_evidence")}>
              <textarea rows={2} value={evidence} onChange={(e) => setEvidence(e.target.value)} />
            </Field>
            <ErrorMessage error={correct.error} />
            <div className="actions">
              <button type="submit" disabled={!start || !initiator.trim() || !reason.trim() || correct.pending}>
                {t("staff.ownership_correct")}
              </button>
              <button type="button" className="secondary" onClick={() => setEditing(false)}>
                {t("common.cancel")}
              </button>
            </div>
          </form>
        ) : (
          <div className="actions">
            <button type="button" className="secondary" onClick={() => setEditing(true)}>
              {t("staff.ownership_correct")}
            </button>
          </div>
        )
      ) : null}
    </div>
  );
}

type OtherGroup = { vehicle_type: string; make: string; make_other: string; model_other: string; vehicles: string[] };

/** § 8.1.11: the authorized support extends the catalog after a check and links the vehicles with
 * «Other» to the accepted value by hand; the vehicles themselves are not merged. */
export function CatalogPage() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const groups = useApi<{ other: OtherGroup[] }>(["staff", "catalog-other"], "/staff/vehicle-catalog/other");
  const [type, setType] = useState("passenger_car");
  const [makeName, setMakeName] = useState("");
  const [makeReason, setMakeReason] = useState("");
  const addMake = useCommand<Record<string, unknown>, { make: { code: string; name: string } }>("/staff/vehicle-catalog/makes");
  const makes = useApi<{ makes: { code: string; name: string }[] }>(
    ["staff", "catalog-makes", type],
    `/staff/vehicle-catalog/makes?type=${type}`,
  );
  const [modelMake, setModelMake] = useState("");
  const [modelName, setModelName] = useState("");
  const [modelReason, setModelReason] = useState("");
  const addModel = useCommand<Record<string, unknown>, unknown>("/staff/vehicle-catalog/models");
  return (
    <div className="page">
      <h1>{t("staff.nav_catalog")}</h1>
      <Card title={t("staff.catalog_other")}>
        <p className="muted small">{t("staff.catalog_other_hint")}</p>
        {groups.isLoading ? <Loading /> : null}
        <ErrorMessage error={groups.error as ApiError | null} />
        {(groups.data?.other ?? []).map((g, i) => (
          <OtherRow key={i} group={g} />
        ))}
        {groups.data && !groups.data.other.length ? <p className="muted">{t("staff.none")}</p> : null}
      </Card>
      <Card title={t("staff.catalog_add_make")}>
        <div className="row">
          <Field label={t("vehicles.vehicle_type")}>
            <select value={type} onChange={(e) => setType(e.target.value)}>
              {["passenger_car", "truck", "bus", "motorcycle", "moped", "other"].map((code) => (
                <option key={code} value={code}>
                  {t(`vehicles.type_${code}`)}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("vehicles.make")}>
            <input value={makeName} onChange={(e) => setMakeName(e.target.value)} />
          </Field>
          <Field label={t("staff.verify_reason")}>
            <input value={makeReason} onChange={(e) => setMakeReason(e.target.value)} />
          </Field>
        </div>
        <ErrorMessage error={addMake.error} />
        <div className="actions">
          <button
            type="button"
            disabled={!makeName.trim() || !makeReason.trim() || addMake.pending}
            onClick={async () => {
              if (await addMake.run({ name: makeName, vehicle_type: type, reason: makeReason })) {
                setMakeName("");
                void client.invalidateQueries({ queryKey: ["staff", "catalog-makes"] });
              }
            }}
          >
            {t("staff.catalog_add")}
          </button>
        </div>
      </Card>
      <Card title={t("staff.catalog_add_model")}>
        <div className="row">
          <Field label={t("vehicles.make")}>
            <select value={modelMake} onChange={(e) => setModelMake(e.target.value)}>
              <option value="">—</option>
              {(makes.data?.makes ?? []).map((m) => (
                <option key={m.code} value={m.code}>
                  {m.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("vehicles.model")}>
            <input value={modelName} onChange={(e) => setModelName(e.target.value)} />
          </Field>
          <Field label={t("staff.verify_reason")}>
            <input value={modelReason} onChange={(e) => setModelReason(e.target.value)} />
          </Field>
        </div>
        <ErrorMessage error={addModel.error} />
        <div className="actions">
          <button
            type="button"
            disabled={!modelMake || !modelName.trim() || !modelReason.trim() || addModel.pending}
            onClick={async () => {
              if (await addModel.run({ make: modelMake, name: modelName, reason: modelReason })) setModelName("");
            }}
          >
            {t("staff.catalog_add")}
          </button>
        </div>
      </Card>
    </div>
  );
}

function OtherRow({ group: g }: { group: OtherGroup }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const makes = useApi<{ makes: { code: string; name: string }[] }>(
    ["staff", "catalog-makes", g.vehicle_type || "passenger_car"],
    `/staff/vehicle-catalog/makes?type=${g.vehicle_type || "passenger_car"}`,
  );
  const [selected, setSelected] = useState<string[]>([]);
  const [make, setMake] = useState(g.make);
  const [model, setModel] = useState("");
  const [reason, setReason] = useState("");
  const models = useApi<{ models: { code: string; name: string }[] }>(
    ["vehicle-models", make],
    `/catalog/vehicle-makes/${encodeURIComponent(make)}/models`,
    Boolean(make),
  );
  const link = useCommand<Record<string, unknown>, unknown>("/staff/vehicle-catalog/link");
  return (
    <div className="list-link stack">
      <span>
        <strong>{[g.make_other || g.make, g.model_other].filter(Boolean).join(" / ")}</strong> ·{" "}
        {t(`vehicles.type_${g.vehicle_type || "other"}`)} · {g.vehicles.length}
      </span>
      <div className="small">
        {g.vehicles.map((id) => (
          <label key={id} style={{ marginRight: 12 }}>
            <input
              type="checkbox"
              checked={selected.includes(id)}
              onChange={(e) => setSelected(e.target.checked ? [...selected, id] : selected.filter((x) => x !== id))}
            />{" "}
            <Link to={`/vehicles/${id}`}>{id}</Link>
          </label>
        ))}
      </div>
      <div className="row">
        <Field label={t("vehicles.make")}>
          <select value={make} onChange={(e) => setMake(e.target.value)}>
            <option value="">—</option>
            {(makes.data?.makes ?? []).map((m) => (
              <option key={m.code} value={m.code}>
                {m.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("vehicles.model")}>
          <select value={model} onChange={(e) => setModel(e.target.value)}>
            <option value="">—</option>
            {(models.data?.models ?? []).map((m) => (
              <option key={m.code} value={m.code}>
                {m.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("staff.verify_reason")}>
          <input value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
      </div>
      <ErrorMessage error={link.error} />
      <div className="actions">
        <button
          type="button"
          className="secondary"
          disabled={!selected.length || !make || !reason.trim() || link.pending}
          onClick={async () => {
            if (await link.run({ vehicles: selected, make, model, reason })) {
              setSelected([]);
              void client.invalidateQueries({ queryKey: ["staff", "catalog-other"] });
            }
          }}
        >
          {t("staff.catalog_link")}
        </button>
      </div>
    </div>
  );
}
