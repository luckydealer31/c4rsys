import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../../api/client";
import type { IncomingOwnerRequest } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Loading } from "../../components/ui";
import { deviceTimeZone, formatMoment } from "../../time";

const QUESTIONS = ["link", "ownership", "management"] as const;

/** Requests of companies to the owner of a vehicle (§ 8.3.1): three separate answers that may be
 * given one by one and are never changed (§ 8.3.14). `base` is `/owner-requests` for a person or
 * `/c/<CMP>/incoming-owner-requests` for a company. */
export function OwnerRequests({ base, title, hideEmpty }: { base: string; title: string; hideEmpty?: boolean }) {
  const { t } = useTranslation();
  const requests = useQuery({
    queryKey: ["incoming-owner-requests", base],
    queryFn: async () => (await api<{ requests: IncomingOwnerRequest[] }>(base)).requests,
  });
  if (requests.isLoading) return <Loading />;
  const open = (requests.data ?? []).filter((r) => r.state === "open");
  if (!open.length && hideEmpty) return null;
  return (
    <>
      <h2>{title}</h2>
      {!open.length ? <p className="muted">{t("links.none_incoming")}</p> : null}
      {open.map((r) => (
        <OwnerRequestAnswer key={r.id} base={base} request={r} />
      ))}
    </>
  );
}

function OwnerRequestAnswer({ base, request }: { base: string; request: IncomingOwnerRequest }) {
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const [choice, setChoice] = useState<Record<string, boolean>>({});
  const reply = useCommand<{ answers: Record<string, boolean> }, unknown>(`${base}/${request.id}/answer`);
  const v = request.vehicle;
  const vehicleText = [v.make, v.model, v.year, v.plate].filter(Boolean).join(" · ");

  return (
    <Card title={request.company.display_name}>
      <p>{t("owner_requests.asks")}</p>
      <p>
        <strong>{vehicleText || v.vehicle_id}</strong>
        {v.vin ? <span className="muted small"> · VIN {v.vin}</span> : null}
      </p>
      <p className="muted small">
        {request.company.public_id} ·{" "}
        {t("links.expires_at", { at: formatMoment(request.expires_at, deviceTimeZone(), i18n.language) })}
      </p>
      {QUESTIONS.map((q) => {
        const given = request.answers[q];
        return (
          <div key={q} style={{ marginBottom: 8 }}>
            <div>{t(`owner_requests.ask_${q}`)}</div>
            {given !== undefined ? (
              <span className={`badge ${given ? "ok" : "warn"}`}>
                {given ? t("owner_requests.yes") : t("owner_requests.no")}
              </span>
            ) : (
              <>
                <label className="small" style={{ marginRight: 16 }}>
                  <input
                    type="radio"
                    name={`${request.id}-${q}`}
                    checked={choice[q] === true}
                    onChange={() => setChoice({ ...choice, [q]: true })}
                  />{" "}
                  {t("owner_requests.yes")}
                </label>
                <label className="small">
                  <input
                    type="radio"
                    name={`${request.id}-${q}`}
                    checked={choice[q] === false}
                    onChange={() => setChoice({ ...choice, [q]: false })}
                  />{" "}
                  {t("owner_requests.no")}
                </label>
              </>
            )}
          </div>
        );
      })}
      <p className="muted small">{t("owner_requests.final")}</p>
      <ErrorMessage error={reply.error} />
      <div className="actions">
        <button
          type="button"
          disabled={!Object.keys(choice).length || reply.pending}
          onClick={async () => {
            if ((await reply.run({ answers: choice })) !== null) {
              setChoice({});
              await client.invalidateQueries({ queryKey: ["incoming-owner-requests", base] });
            }
          }}
        >
          {t("owner_requests.answer")}
        </button>
      </div>
    </Card>
  );
}
