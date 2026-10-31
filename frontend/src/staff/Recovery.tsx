import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router";

import type { ApiError } from "../api/client";
import { useApi } from "../api/queries";
import { useCommand } from "../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../components/ui";
import { ReasonForm, useStaff, useStaffCan, useWhen } from "./common";

const STATES = ["new", "in_review", "info_requested", "proposed", "approved", "completed", "refused"] as const;

type Case = {
  id: string;
  number: number;
  state: (typeof STATES)[number];
  identifier: string;
  known_contact: string;
  new_email: string;
  created_at: string;
  user: { public_id: string; name: string; state: string } | null;
  urgent: boolean;
  approved_at: string | null;
  effective_at: string | null;
  completed_at: string | null;
  version: number;
  description?: string;
  ticket_id?: string | null;
  known_contact_matches?: boolean | null;
  account_contacts?: { id: string; kind: string; value: string; unavailable: boolean }[];
  proposed_by?: string | null;
  proposed_by_member_id?: string | null;
  proposed_at?: string | null;
  assessment?: string;
  unavailable_contact_ids?: string[];
  urgent_basis?: string;
  approvals?: { member_id: string; name: string; comment: string; at: string; void: boolean }[];
  refusal_reason?: string;
  history?: { at: string; action: string; actor: string | null; process: string | null; reason: string }[];
};

/** § 2.3.1: the requests to recover access through the support. Switched off by the platform
 * setting until the regulation of the check is ready (decision В-11.2). */
export function RecoveryCasesPage() {
  const { t } = useTranslation();
  const when = useWhen();
  const [state, setState] = useState("");
  const list = useApi<{ cases: Case[] }>(["staff", "recovery", state], `/staff/recovery${state ? `?state=${state}` : ""}`);
  const disabled = (list.error as ApiError | null)?.code === "RECOVERY_DISABLED";
  return (
    <div className="page">
      <h1>{t("staff.nav_recovery")}</h1>
      {disabled ? (
        <div className="alert warn" role="status">
          {t("staff.recovery_disabled")}
        </div>
      ) : (
        <>
          <div className="actions">
            <select value={state} onChange={(e) => setState(e.target.value)} aria-label={t("staff.recovery_state")}>
              <option value="">{t("staff.ticket_all")}</option>
              {STATES.map((s) => (
                <option key={s} value={s}>
                  {t(`staff.recovery_state_${s}`)}
                </option>
              ))}
            </select>
          </div>
          {list.isLoading ? <Loading /> : null}
          <ErrorMessage error={list.error as ApiError | null} />
          <div className="stack">
            {list.data?.cases.map((c) => (
              <Link key={c.id} to={`/recovery/${c.id}`} className="list-link">
                <strong>№ {c.number}</strong> · {t(`staff.recovery_state_${c.state}`)} · {c.identifier}
                {c.urgent ? ` · ${t("staff.recovery_urgent")}` : ""} · <span className="muted small">{when(c.created_at)}</span>
              </Link>
            ))}
            {list.data && !list.data.cases.length ? <p className="muted">{t("staff.none")}</p> : null}
          </div>
        </>
      )}
    </div>
  );
}

export function RecoveryCasePage() {
  const { t } = useTranslation();
  const { id = "" } = useParams();
  const when = useWhen();
  const can = useStaffCan();
  const me = useStaff();
  const client = useQueryClient();
  const key = ["staff", "recovery-case", id];
  const found = useApi<{ case: Case }>(key, `/staff/recovery/${id}`);
  const set = (c: Case | undefined) => {
    if (c) {
      client.setQueryData(key, { case: c });
      void client.invalidateQueries({ queryKey: ["staff", "recovery"] });
    }
  };
  const account = useCommand<{ user: string; version: number }, { case: Case }>(`/staff/recovery/${id}/account`);
  const ticket = useCommand<{ ticket_number: number; version: number }, { case: Case }>(`/staff/recovery/${id}/ticket`);
  const info = useCommand<{ comment: string; version: number }, { case: Case }>(`/staff/recovery/${id}/info`);
  const propose = useCommand<Record<string, unknown>, { case: Case }>(`/staff/recovery/${id}/propose`);
  const approve = useCommand<{ comment: string; version: number }, { case: Case }>(`/staff/recovery/${id}/approve`);
  const refuse = useCommand<{ reason: string; version: number }, { case: Case }>(`/staff/recovery/${id}/refuse`);
  const [usr, setUsr] = useState("");
  const [ticketNumber, setTicketNumber] = useState("");
  const [assessment, setAssessment] = useState("");
  const [unavailable, setUnavailable] = useState<string[]>([]);
  const [urgent, setUrgent] = useState(false);
  const [basis, setBasis] = useState("");
  const [comment, setComment] = useState("");
  const [mode, setMode] = useState<"" | "info" | "refuse">("");
  if (found.isLoading) return <Loading />;
  if (found.error || !found.data) return <ErrorMessage error={found.error as ApiError | null} />;
  const c = found.data.case;
  const open = ["new", "in_review", "info_requested", "proposed"].includes(c.state);
  const confirmations = (c.approvals ?? []).filter((a) => !a.void);
  const mine = c.proposed_by_member_id === me.member.id;
  return (
    <div className="page">
      <p>
        <Link to="/recovery">← {t("staff.nav_recovery")}</Link>
      </p>
      <h1>
        {t("staff.recovery_case", { number: c.number })} · {t(`staff.recovery_state_${c.state}`)}
      </h1>
      <Card title={t("staff.recovery_applicant")}>
        <dl className="facts">
          <dt>{t("staff.recovery_identifier")}</dt>
          <dd>{c.identifier}</dd>
          <dt>{t("staff.recovery_known_contact")}</dt>
          <dd>
            {c.known_contact}
            {c.known_contact_matches === true ? ` · ${t("staff.recovery_matches")}` : ""}
            {c.known_contact_matches === false ? ` · ${t("staff.recovery_no_match")}` : ""}
          </dd>
          <dt>{t("staff.recovery_new_email")}</dt>
          <dd>{c.new_email ? `${c.new_email} · ${t("staff.recovery_new_email_confirmed")}` : "—"}</dd>
          <dt>{t("staff.recovery_description")}</dt>
          <dd>{c.description || "—"}</dd>
          <dt>{t("staff.recovery_received")}</dt>
          <dd>{when(c.created_at)}</dd>
        </dl>
        <p className="muted small">{t("staff.recovery_check_hint")}</p>
      </Card>
      <Card title={t("staff.recovery_account")}>
        {c.user ? (
          <p>
            <Link to={`/users/${c.user.public_id}`}>
              {c.user.name || "—"} ({c.user.public_id})
            </Link>
          </p>
        ) : (
          <p className="muted">{t("staff.recovery_no_account")}</p>
        )}
        {open ? (
          <form
            className="row"
            onSubmit={async (e) => {
              e.preventDefault();
              set((await account.run({ user: usr.trim(), version: c.version }))?.case);
            }}
          >
            <input value={usr} placeholder="USR-…" onChange={(e) => setUsr(e.target.value)} aria-label={t("staff.recovery_set_account")} />
            <button type="submit" className="secondary" disabled={account.pending || !usr.trim()}>
              {t("staff.recovery_set_account")}
            </button>
          </form>
        ) : null}
        <ErrorMessage error={account.error} />
        <form
          className="row"
          onSubmit={async (e) => {
            e.preventDefault();
            set((await ticket.run({ ticket_number: Number(ticketNumber), version: c.version }))?.case);
          }}
        >
          <input
            inputMode="numeric"
            value={ticketNumber}
            placeholder={t("staff.ticket_number")}
            onChange={(e) => setTicketNumber(e.target.value.replace(/\D/g, ""))}
            aria-label={t("staff.ticket_number")}
          />
          <button type="submit" className="secondary" disabled={ticket.pending || !ticketNumber}>
            {t("staff.recovery_link_ticket")}
          </button>
          {c.ticket_id ? <Link to={`/tickets/${c.ticket_id}`}>{t("staff.recovery_ticket")}</Link> : null}
        </form>
        <ErrorMessage error={ticket.error} />
      </Card>
      {c.user && open ? (
        <Card title={t("staff.recovery_propose")}>
          <form
            className="stack"
            onSubmit={async (e) => {
              e.preventDefault();
              set(
                (
                  await propose.run({
                    assessment,
                    unavailable_contacts: unavailable,
                    urgent,
                    urgent_basis: urgent ? basis : "",
                    version: c.version,
                  })
                )?.case,
              );
            }}
          >
            <Field label={t("staff.recovery_assessment")} hint={t("staff.recovery_assessment_hint")}>
              <textarea value={assessment} onChange={(e) => setAssessment(e.target.value)} rows={3} />
            </Field>
            <fieldset>
              <legend>{t("staff.recovery_unavailable")}</legend>
              {(c.account_contacts ?? []).map((x) => (
                <label key={x.id} className="check">
                  <input
                    type="checkbox"
                    checked={unavailable.includes(x.id)}
                    disabled={x.unavailable}
                    onChange={(e) => setUnavailable(e.target.checked ? [...unavailable, x.id] : unavailable.filter((v) => v !== x.id))}
                  />{" "}
                  {t(`staff.contact_kind_${x.kind}`, { defaultValue: x.kind })}: {x.value}
                  {x.unavailable ? ` · ${t("staff.contact_unavailable")}` : ""}
                </label>
              ))}
            </fieldset>
            <label className="check">
              <input type="checkbox" checked={urgent} onChange={(e) => setUrgent(e.target.checked)} /> {t("staff.recovery_urgent")}
            </label>
            {urgent ? (
              <Field label={t("staff.recovery_urgent_basis")} hint={t("staff.recovery_urgent_hint")}>
                <textarea value={basis} onChange={(e) => setBasis(e.target.value)} rows={2} />
              </Field>
            ) : null}
            <ErrorMessage error={propose.error} />
            <div className="actions">
              <button type="submit" disabled={propose.pending || !assessment.trim() || (urgent && !basis.trim())}>
                {t("staff.recovery_propose_submit")}
              </button>
            </div>
          </form>
        </Card>
      ) : null}
      {c.state !== "new" && c.assessment ? (
        <Card title={t("staff.recovery_decision")}>
          <p>{c.assessment}</p>
          <p className="muted small">
            {c.proposed_by} · {when(c.proposed_at)}
            {c.urgent ? ` · ${t("staff.recovery_urgent")}: ${c.urgent_basis ?? ""}` : ""}
          </p>
          <h3>{t("staff.recovery_confirmations", { count: confirmations.length })}</h3>
          <ul className="plain">
            {(c.approvals ?? []).map((a) => (
              <li key={`${a.member_id}-${a.at}`} className={a.void ? "muted" : ""}>
                {a.name} · {when(a.at)} {a.comment ? `· ${a.comment}` : ""} {a.void ? `· ${t("staff.recovery_void")}` : ""}
              </li>
            ))}
          </ul>
          {c.effective_at ? <p>{t("staff.recovery_effective", { when: when(c.effective_at) })}</p> : null}
          {c.state === "proposed" && can("recovery.approve") ? (
            mine ? (
              <p className="muted small">{t("staff.recovery_initiator_hint")}</p>
            ) : (
              <div className="stack">
                <Field label={t("staff.recovery_comment")}>
                  <input value={comment} onChange={(e) => setComment(e.target.value)} />
                </Field>
                <ErrorMessage error={approve.error} />
                <div className="actions">
                  <button
                    type="button"
                    disabled={approve.pending}
                    onClick={async () => set((await approve.run({ comment, version: c.version }))?.case)}
                  >
                    {t("staff.recovery_approve")}
                  </button>
                </div>
              </div>
            )
          ) : null}
        </Card>
      ) : null}
      {open || c.state === "approved" ? (
        <div className="actions">
          {open ? (
            <button type="button" className="secondary" onClick={() => setMode("info")}>
              {t("staff.recovery_request_info")}
            </button>
          ) : null}
          <button type="button" className="danger" onClick={() => setMode("refuse")}>
            {t("staff.recovery_refuse")}
          </button>
        </div>
      ) : null}
      {mode === "info" ? (
        <Card title={t("staff.recovery_request_info")}>
          <ReasonForm
            label={t("staff.recovery_info_text")}
            submit={t("staff.recovery_request_info")}
            pending={info.pending}
            error={info.error}
            onSubmit={async (text) => {
              set((await info.run({ comment: text, version: c.version }))?.case);
              setMode("");
            }}
            onCancel={() => setMode("")}
          />
        </Card>
      ) : null}
      {mode === "refuse" ? (
        <Card title={t("staff.recovery_refuse")}>
          <ReasonForm
            label={t("staff.recovery_refusal_reason")}
            submit={t("staff.recovery_refuse")}
            pending={refuse.pending}
            error={refuse.error}
            onSubmit={async (text) => {
              set((await refuse.run({ reason: text, version: c.version }))?.case);
              setMode("");
            }}
            onCancel={() => setMode("")}
          />
        </Card>
      ) : null}
      {c.refusal_reason ? (
        <Card title={t("staff.recovery_refusal_reason")}>
          <p>{c.refusal_reason}</p>
        </Card>
      ) : null}
      <Card title={t("staff.recovery_history")}>
        <ul className="plain">
          {(c.history ?? []).map((h, index) => (
            <li key={`${h.at}-${index}`} className="small">
              {when(h.at)} · {t(`staff.recovery_action_${h.action.replace("recovery.", "")}`, { defaultValue: h.action })} ·{" "}
              {h.actor ?? h.process ?? ""} {h.reason ? `· ${h.reason}` : ""}
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
