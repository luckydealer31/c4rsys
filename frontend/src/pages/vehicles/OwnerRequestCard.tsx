import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "../../api/queries";
import type { OwnerRequest, Vehicle } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field } from "../../components/ui";
import { useCan, useCompany } from "../../layout/CompanyShell";
import { deviceTimeZone, formatMoment } from "../../time";

/** Why a request was closed without a cancel: another owner was declared. */

/** The request to the owner of a managed vehicle with its three separate answers (§ 8.3.1,
 * § 8.3.13–8.3.14): who answered and when, the conflict after a refusal (§ 8.3.3), a new request
 * after a refusal or an incomplete answer. */
export function OwnerRequestCard({ vehicle }: { vehicle: Vehicle }) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const queryClient = useQueryClient();
  const base = `/c/${company.public_id}/vehicles/${vehicle.id}/owner-requests`;
  const key = ["owner-requests", company.public_id, vehicle.id];
  const requests = useApi<{ requests: OwnerRequest[] }>(key, base);
  const send = useCommand<{ owner_lookup?: string }, unknown>(base);
  const cancel = useCommand<Record<string, never>, unknown>(`${base}/cancel`);
  const when = (value: string) => formatMoment(value, company.timezone || deviceTimeZone(), i18n.language);
  const owner = vehicle.owner;
  const last = requests.data?.requests[0];
  const editable = can("vehicle.edit");
  // The owner is named here by his ID and asked at once (customer's remark of 25.11.2026); the
  // owner already given as a profile stands in the field.
  const [lookup, setLookup] = useState<string | null>(null);
  const given = owner?.kind === "profile" ? owner.lookup : "";
  const typed = lookup ?? given;

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: key });
    await queryClient.invalidateQueries({ queryKey: ["vehicle", company.public_id, vehicle.id] });
  }

  const canSend = editable && !owner?.confirmed && (!last || last.state !== "open");
  // Only the last request is shown; the earlier ones — withdrawn, declined, to another owner —
  // are the history and open by a line under it (customer's remark of 26.11.2026).
  const [earlier, setEarlier] = useState(false);
  const all = requests.data?.requests ?? [];
  const shown = earlier ? all : all.slice(0, 1);

  return (
    <div style={{ maxWidth: 760 }}>
    <Card title={t("owner_requests.title")}>
      {owner?.confirmed && !last ? <p><span className="badge ok">{t("owner_requests.confirmed")}</span></p> : null}
      {owner?.conflict ? <p><span className="badge warn">{t("owner_requests.conflict")}</span></p> : null}
      {canSend ? (
        <div className="row short-fields" style={{ alignItems: "flex-end" }}>
          <Field label={t("owner_requests.lookup")} info={t("owner_requests.lookup_hint")}>
            <input value={typed} onChange={(e) => setLookup(e.target.value)} />
          </Field>
          <button
            type="button"
            className="small-button"
            style={{ flex: "none" }}
            disabled={!typed.trim() || send.pending}
            onClick={async () => {
              if ((await send.run({ owner_lookup: typed.trim() })) !== null) {
                setLookup(null);
                await refresh();
              }
            }}
          >
            {last ? t("owner_requests.send_again") : t("owner_requests.send")}
          </button>
        </div>
      ) : null}
      {shown.map((r) => {
        // One line: the state, whom it was sent to, when it was sent and answered. The answers
        // stand in a row beside each other (customer's remark of 26.11.2026). The name of the
        // one who answered is told only for a company: a person answers for himself.
        const times = new Set(r.answers.map((a) => a.at));
        const people = new Set(r.answers.map((a) => a.by));
        const together = r.answers.length > 0 && times.size === 1 && people.size === 1;
        const forCompany = r.target.toUpperCase().startsWith("CMP-");
        const first = r.answers[0];
        return (
          <div key={r.id} style={{ margin: "10px 0 8px" }} data-testid="owner-request">
            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              {r === last && owner?.confirmed ? (
                <span className="badge ok">{t("owner_requests.confirmed")}</span>
              ) : (
                <span className={`badge ${r.state === "complete" ? "ok" : r.state === "open" || r.state === "awaiting_rest" ? "warn" : ""}`}>
                  {t(`owner_requests.state_${r.state}`)}
                </span>
              )}
              <span>{r.target}</span>
              <span className="muted small">
                {t("links.sent_at", { at: when(r.created_at) })}
                {r.state === "open" || r.state === "awaiting_rest"
                  ? ` · ${t("links.expires_at", { at: when(r.expires_at) })}`
                  : ""}
                {together && first
                  ? ` · ${t("links.decided_at", { at: when(first.at) })}${forCompany ? ` (${first.by})` : ""}`
                  : ""}
              </span>
            </div>
            {r.answers.length ? (
              <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginTop: 6 }} className="small">
                {r.answers.map((a) => (
                  <span key={a.question}>
                    {t(`owner_requests.question_${a.question}`)}{" "}
                    <span className={`badge ${a.answer ? "ok" : "warn"}`}>
                      {a.answer ? t("owner_requests.yes") : t("owner_requests.no")}
                    </span>
                    {together ? null : (
                      <span className="muted"> {forCompany ? `${a.by} · ` : ""}{when(a.at)}</span>
                    )}
                  </span>
                ))}
              </div>
            ) : null}
            {r.closed_reason === "link_broken" ? (
              <p className="muted small">{t("owner_requests.closed_link_broken")}</p>
            ) : null}
            {r.closed_reason === "owner_changed" ? (
              <p className="muted small">{t("owner_requests.closed_owner_changed")}</p>
            ) : null}
            {r.ownership_dispute ? <p className="alert warn">{t("owner_requests.dispute")}</p> : null}
            {r.by_phone && r.state === "open" ? <p className="muted small">{t("links.no_sms")}</p> : null}
          </div>
        );
      })}
      {all.length > 1 ? (
        <button type="button" className="link small" data-testid="owner-requests-earlier" onClick={() => setEarlier(!earlier)}>
          {earlier ? t("owner_requests.earlier_hide") : t("owner_requests.earlier_show", { count: all.length - 1 })}
        </button>
      ) : null}
      <ErrorMessage error={send.error ?? cancel.error} />
      <div className="actions">
        {editable && last && (last.state === "open" || last.state === "awaiting_rest") ? (
          <button
            type="button"
            className="secondary"
            disabled={cancel.pending}
            onClick={async () => {
              if ((await cancel.run({})) !== null) await refresh();
            }}
          >
            {t("links.cancel")}
          </button>
        ) : null}
      </div>
    </Card>
    </div>
  );
}
