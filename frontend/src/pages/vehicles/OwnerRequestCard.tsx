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

  // After the owner took his consent back a new request is not offered (customer's remark of
  // 26.11.2026): the management ends or goes on with the former owner and the warning.
  const canSend = editable && !owner?.confirmed && !owner?.withdrawn_at && (!last || last.state !== "open");
  // To the same owner the request may be sent anew — he took the consent back by mistake or
  // changed his mind — by one button, with nothing to enter (customer's decision of 26.11.2026).
  const canResend = editable && Boolean(owner?.withdrawn_at) && (!last || last.state !== "open");
  // Only the last request is shown; the earlier ones — withdrawn, declined, to another owner —
  // are the history and open by a line under it (customer's remark of 26.11.2026).
  const [earlier, setEarlier] = useState(false);
  const all = requests.data?.requests ?? [];
  const shown = earlier ? all : all.slice(0, 1);

  return (
    <>
    <Card title={t("owner_requests.title")}>
      {owner?.confirmed && !last ? <p><span className="badge ok">{t("owner_requests.confirmed")}</span></p> : null}
      {owner?.conflict ? <p><span className="badge warn">{t("owner_requests.conflict")}</span></p> : null}
      {owner?.withdrawn_at ? (
        <p className="alert warn small" data-testid="owner-withdrew">
          {t("owner_requests.withdrew", { at: when(owner.withdrawn_at) })}
        </p>
      ) : null}
      {canResend ? (
        <div className="actions">
          <button
            type="button"
            className="secondary small"
            data-testid="owner-request-again"
            disabled={send.pending}
            onClick={async () => {
              if ((await send.run({})) !== null) await refresh();
            }}
          >
            {t("owner_requests.send_same")}
          </button>
        </div>
      ) : null}
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
        // stand in a column, like the categories in the block beside (customer's remarks of
        // 26.11.2026). The name of the
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
              <div style={{ marginTop: 8 }}>
                {r.answers.map((a) => (
                  <div key={a.question} style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                    <span className={`badge ${a.answer ? "ok" : "warn"}`} style={{ minWidth: 34, textAlign: "center" }}>
                      {a.answer ? t("owner_requests.yes") : t("owner_requests.no")}
                    </span>
                    <span>{t(`owner_requests.question_${a.question}`)}</span>
                    {together ? null : (
                      <span className="muted small">{forCompany ? `${a.by} · ` : ""}{when(a.at)}</span>
                    )}
                  </div>
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
    </>
  );
}
