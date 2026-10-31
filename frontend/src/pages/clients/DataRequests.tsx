import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../../api/client";
import { useApi } from "../../api/queries";
import type { DataRequest, IncomingDataRequest } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field } from "../../components/ui";
import { useCompany } from "../../layout/CompanyShell";
import { deviceTimeZone, formatMoment } from "../../time";
import { orderedFields, sharedValue } from "./ProfileLinkCard";

/** § 6.4.4: a request of fresh data from the linked profile, only when the client asks for a
 * service; the answer is a new snapshot whose values are taken one by one (§ 6.4.2). */
export function DataRequestsPanel({
  linkId,
  editable,
  applicable,
  version,
  onApplied,
}: {
  linkId: string;
  editable: boolean;
  applicable: string[];
  version: number;
  onApplied: () => Promise<void>;
}) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const queryClient = useQueryClient();
  const base = `/c/${company.public_id}`;
  const key = ["data-requests", company.public_id, linkId];
  const requests = useApi<{ requests: DataRequest[] }>(key, `${base}/profile-links/${linkId}/data-requests`);
  const send = useCommand<{ occasion: string }, unknown>(`${base}/profile-links/${linkId}/data-requests`);
  const cancel = useCommand<{ id: string }, unknown>((b) => `${base}/data-requests/${b.id}/cancel`);
  const apply = useCommand<{ field: string; expected_version: number; data_request_id: string }, unknown>(
    `${base}/profile-links/${linkId}/apply`,
  );
  const [occasion, setOccasion] = useState("");
  const when = (value: string) => formatMoment(value, company.timezone || deviceTimeZone(), i18n.language);
  const items = requests.data?.requests ?? [];
  const pending = items.find((r) => r.state === "pending");

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: key });
  }

  return (
    <div className="stack">
      <h3>{t("data_requests.title")}</h3>
      <p className="muted small">{t("data_requests.hint")}</p>
      {editable && !pending ? (
        <div className="row">
          <Field label={t("data_requests.occasion")}>
            <input value={occasion} onChange={(e) => setOccasion(e.target.value)} />
          </Field>
          <div className="actions">
            <button
              type="button"
              className="secondary"
              disabled={!occasion.trim() || send.pending}
              onClick={async () => {
                if ((await send.run({ occasion })) !== null) {
                  setOccasion("");
                  await refresh();
                }
              }}
            >
              {t("data_requests.send")}
            </button>
          </div>
        </div>
      ) : null}
      {items.map((r) => (
        <div key={r.id} className="stack">
          <p className="small">
            <span className={`badge ${r.state === "accepted" ? "ok" : r.state === "pending" ? "warn" : ""}`}>
              {t(`data_requests.state_${r.state}`)}
            </span>{" "}
            {r.occasion} · {when(r.created_at)}
            {r.decided_at ? ` · ${when(r.decided_at)}` : ""}
          </p>
          {r.state === "pending" && editable ? (
            <div className="actions">
              <button
                type="button"
                className="secondary small"
                disabled={cancel.pending}
                onClick={async () => {
                  if ((await cancel.run({ id: r.id })) !== null) await refresh();
                }}
              >
                {t("links.cancel")}
              </button>
            </div>
          ) : null}
          {r.state === "accepted" && Object.keys(r.shared).length ? (
            <table className="table">
              <tbody>
                {orderedFields(r.shared).map(([field, value]) => (
                  <tr key={field}>
                    <td>{t(`links.field_${field}`)}</td>
                    <td>{sharedValue(value)}</td>
                    <td>
                      {editable && applicable.includes(field) ? (
                        <button
                          type="button"
                          className="secondary small"
                          disabled={apply.pending}
                          onClick={async () => {
                            const body = { field, expected_version: version, data_request_id: r.id };
                            if ((await apply.run(body)) !== null) await onApplied();
                          }}
                        >
                          {t("links.apply")}
                        </button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
        </div>
      ))}
      <ErrorMessage error={send.error ?? cancel.error ?? apply.error} />
    </div>
  );
}

/** The recipient's side (§ 6.3.8): a person in the cabinet, a company in its interface. */
export function IncomingDataRequests({ base, title }: { base: string; title: string }) {
  const requests = useQuery({
    queryKey: ["incoming-data-requests", base],
    queryFn: async () => (await api<{ requests: IncomingDataRequest[] }>(base)).requests,
  });
  const pending = (requests.data ?? []).filter((r) => r.state === "pending");
  if (!pending.length) return null;
  return (
    <>
      <h2>{title}</h2>
      {pending.map((r) => (
        <DataRequestCard key={r.id} base={base} request={r} />
      ))}
    </>
  );
}

function DataRequestCard({ base, request }: { base: string; request: IncomingDataRequest }) {
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const offered = orderedFields(request.available).filter(([, value]) => value !== "" && value !== null);
  const [chosen, setChosen] = useState<Set<string>>(() => new Set());
  const accept = useCommand<{ fields: string[] }, unknown>(`${base}/${request.id}/accept`);
  const decline = useCommand<Record<string, never>, unknown>(`${base}/${request.id}/decline`);

  async function done() {
    await client.invalidateQueries({ queryKey: ["incoming-data-requests", base] });
    await client.invalidateQueries({ queryKey: ["notifications"] });
  }

  return (
    <Card title={request.company.display_name}>
      <p>{t("data_requests.asks", { occasion: request.occasion })}</p>
      <p className="muted small">
        {request.company.public_id} ·{" "}
        {t("links.expires_at", { at: formatMoment(request.expires_at, deviceTimeZone(), i18n.language) })}
      </p>
      <p className="small">{t("data_requests.choose")}</p>
      {offered.map(([field, value]) => (
        <div key={field}>
          <label className="small">
            <input
              type="checkbox"
              checked={chosen.has(field)}
              onChange={() => {
                const next = new Set(chosen);
                if (next.has(field)) next.delete(field);
                else next.add(field);
                setChosen(next);
              }}
            />{" "}
            {t(`links.field_${field}`)}: <span className="muted">{sharedValue(value)}</span>
          </label>
        </div>
      ))}
      <ErrorMessage error={accept.error ?? decline.error} />
      <div className="actions">
        <button
          type="button"
          disabled={accept.pending || chosen.size === 0}
          onClick={async () => {
            if ((await accept.run({ fields: [...chosen] })) !== null) await done();
          }}
        >
          {t("data_requests.give")}
        </button>
        <button
          type="button"
          className="secondary"
          disabled={decline.pending}
          onClick={async () => {
            if ((await decline.run({})) !== null) await done();
          }}
        >
          {t("links.decline")}
        </button>
      </div>
    </Card>
  );
}
