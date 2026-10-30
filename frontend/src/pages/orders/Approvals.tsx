import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "../../api/queries";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { useCan, useCompany } from "../../layout/CompanyShell";
import { deviceTimeZone, formatMoment, fromLocalInput, toLocalInput } from "../../time";

export type Approval = {
  id: string;
  item_type: string;
  item_id: string;
  title: string;
  state: "pending" | "approved" | "declined" | "withdrawn";
  requested_at: string;
  proceed_at: string | null;
  answered_at: string | null;
  answer_source: "" | "client" | "employee";
  contact_method: string;
  comment: string;
};

const METHODS = ["phone", "messenger", "email", "in_person", "document", "other"];

/** Positions added to an order in work that wait for the client's answer (§ 12.3; decisions
 * З9, З10): «Do without the client's confirmation» and the answer received outside the system. */
export function OrderApprovals({ orderId, onChange }: { orderId: string; onChange: () => Promise<void> }) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const key = ["approvals", company.public_id, orderId];
  const approvals = useApi<{ approvals: Approval[] }>(key, `/c/${company.public_id}/orders/${orderId}/approvals`);
  const zone = company.timezone || deviceTimeZone();
  const when = (v: string | null) => formatMoment(v, zone, i18n.language);
  if (approvals.isLoading) return <Loading />;
  const items = approvals.data?.approvals ?? [];
  if (!items.length) return null;
  return (
    <Card title={t("approvals.title")}>
      {items.map((a) => (
        <div key={a.id} style={{ padding: "8px 0", borderBottom: "1px solid var(--border, #ddd)" }}>
          <div>
            <strong>{a.title || t(`approvals.item_${a.item_type}`)}</strong>{" "}
            <span className={`badge ${a.state === "approved" ? "ok" : a.state === "pending" ? "warn" : ""}`}>
              {t(`approvals.state_${a.state}`)}
            </span>{" "}
            {a.proceed_at && a.state === "pending" ? <span className="badge">{t("approvals.proceeding")}</span> : null}
          </div>
          <div className="muted small">
            {t(`approvals.item_${a.item_type}`)} · {t("approvals.requested", { at: when(a.requested_at) })}
            {a.answered_at
              ? ` · ${t(`approvals.source_${a.answer_source}`)} ${when(a.answered_at)}${a.contact_method ? `, ${t(`approvals.method_${a.contact_method}`)}` : ""}${a.comment ? `: ${a.comment}` : ""}`
              : ""}
          </div>
          {a.state === "pending" ? <ApprovalActions orderId={orderId} approval={a} queryKey={key} onChange={onChange} /> : null}
        </div>
      ))}
    </Card>
  );
}

function ApprovalActions({
  orderId,
  approval,
  queryKey,
  onChange,
}: {
  orderId: string;
  approval: Approval;
  queryKey: unknown[];
  onChange: () => Promise<void>;
}) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const base = `/c/${company.public_id}/orders/${orderId}/approvals/${approval.id}`;
  const proceed = useCommand<Record<string, never>, unknown>(`${base}/proceed`);
  const answer = useCommand<Record<string, string>, unknown>(`${base}/answer`);
  const zone = company.timezone || deviceTimeZone();
  const [open, setOpen] = useState(false);
  const [decision, setDecision] = useState("approved");
  const [method, setMethod] = useState("phone");
  const [at, setAt] = useState(toLocalInput(new Date(), zone));
  const [comment, setComment] = useState("");
  if (!can("order.edit")) return null;

  async function done() {
    await client.invalidateQueries({ queryKey });
    await onChange();
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const body = { decision, method, comment, answered_at: fromLocalInput(at, zone) };
    if ((await answer.run(body)) !== null) {
      setOpen(false);
      await done();
    }
  }

  return (
    <>
      <div className="actions">
        <button type="button" className="secondary small" onClick={() => setOpen(!open)}>
          {t("approvals.record_answer")}
        </button>
        {!approval.proceed_at ? (
          <button
            type="button"
            className="secondary small"
            disabled={proceed.pending}
            onClick={async () => {
              if ((await proceed.run({})) !== null) await done();
            }}
          >
            {t("approvals.proceed")}
          </button>
        ) : null}
      </div>
      {open ? (
        <form className="row" onSubmit={submit}>
          <Field label={t("approvals.decision")}>
            <select value={decision} onChange={(e) => setDecision(e.target.value)}>
              <option value="approved">{t("approvals.state_approved")}</option>
              <option value="declined">{t("approvals.state_declined")}</option>
            </select>
          </Field>
          <Field label={t("approvals.method")}>
            <select value={method} onChange={(e) => setMethod(e.target.value)}>
              {METHODS.map((m) => (
                <option key={m} value={m}>
                  {t(`approvals.method_${m}`)}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("approvals.answered_at")}>
            <input type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} />
          </Field>
          <Field label={t("approvals.comment")}>
            <input value={comment} onChange={(e) => setComment(e.target.value)} />
          </Field>
          <div className="actions">
            <button type="submit" disabled={answer.pending}>
              {t("common.save")}
            </button>
          </div>
        </form>
      ) : null}
      {decision === "declined" && open ? <p className="muted small">{t("approvals.decline_hint")}</p> : null}
      <ErrorMessage error={proceed.error ?? answer.error} />
    </>
  );
}

/** «Agree additions with the client» of the company (§ 12.3.5; decision З9): off by default. */
export function ApprovalPolicyCard() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const key = ["order-policy", company.public_id];
  const policy = useApi<{ require_approval: boolean }>(key, `/c/${company.public_id}/order-policy`);
  const save = useCommand<{ require_approval: boolean }, unknown>(`/c/${company.public_id}/order-policy`, "PUT");
  if (policy.isLoading) return <Loading />;
  const value = policy.data?.require_approval ?? false;
  return (
    <Card title={t("approvals.policy_title")}>
      <label>
        <input
          type="checkbox"
          checked={value}
          disabled={!can("company.settings") || save.pending}
          onChange={async () => {
            if ((await save.run({ require_approval: !value })) !== null) {
              await client.invalidateQueries({ queryKey: key });
            }
          }}
        />{" "}
        {t("approvals.policy_label")}
      </label>
      <p className="muted small">{t("approvals.policy_hint")}</p>
      <ErrorMessage error={save.error} />
    </Card>
  );
}
