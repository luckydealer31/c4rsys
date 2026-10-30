import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { useApi } from "../../api/queries";
import type { OwnerRequest, Vehicle } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage } from "../../components/ui";
import { useCan, useCompany } from "../../layout/CompanyShell";
import { deviceTimeZone, formatMoment } from "../../time";

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
  const send = useCommand<Record<string, never>, unknown>(base);
  const cancel = useCommand<Record<string, never>, unknown>(`${base}/cancel`);
  const when = (value: string) => formatMoment(value, company.timezone || deviceTimeZone(), i18n.language);
  const owner = vehicle.owner;
  const last = requests.data?.requests[0];
  const editable = can("vehicle.edit");

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: key });
    await queryClient.invalidateQueries({ queryKey: ["vehicle", company.public_id, vehicle.id] });
  }

  const canSend =
    editable &&
    owner?.kind === "profile" &&
    !owner.confirmed &&
    (!last || last.state !== "open");

  return (
    <Card title={t("owner_requests.title")}>
      {owner?.confirmed ? <p><span className="badge ok">{t("owner_requests.confirmed")}</span></p> : null}
      {owner?.conflict ? <p><span className="badge warn">{t("owner_requests.conflict")}</span></p> : null}
      {owner?.kind !== "profile" ? <p className="muted">{t("owner_requests.profile_needed")}</p> : null}
      {(requests.data?.requests ?? []).map((r) => (
        <div key={r.id} className="stack" style={{ marginBottom: 12 }}>
          <p>
            <span className={`badge ${r.state === "complete" ? "ok" : r.state === "open" || r.state === "awaiting_rest" ? "warn" : ""}`}>
              {t(`owner_requests.state_${r.state}`)}
            </span>{" "}
            {r.target}
          </p>
          <p className="muted small">
            {t("links.sent_at", { at: when(r.created_at) })}
            {r.state === "open" || r.state === "awaiting_rest"
              ? ` · ${t("links.expires_at", { at: when(r.expires_at) })}`
              : ""}
          </p>
          {r.answers.length ? (
            <table className="table">
              <tbody>
                {r.answers.map((a) => (
                  <tr key={a.question}>
                    <td>{t(`owner_requests.question_${a.question}`)}</td>
                    <td>
                      <span className={`badge ${a.answer ? "ok" : "warn"}`}>
                        {a.answer ? t("owner_requests.yes") : t("owner_requests.no")}
                      </span>
                    </td>
                    <td className="small muted">
                      {a.by} · {when(a.at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
          {r.ownership_dispute ? <p className="alert warn">{t("owner_requests.dispute")}</p> : null}
          {r.by_phone && r.state === "open" ? <p className="muted small">{t("links.no_sms")}</p> : null}
        </div>
      ))}
      <ErrorMessage error={send.error ?? cancel.error} />
      <div className="actions">
        {canSend ? (
          <button
            type="button"
            disabled={send.pending}
            onClick={async () => {
              if ((await send.run({})) !== null) await refresh();
            }}
          >
            {last ? t("owner_requests.send_again") : t("owner_requests.send")}
          </button>
        ) : null}
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
  );
}
