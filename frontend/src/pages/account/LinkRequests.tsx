import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../../api/client";
import type { IncomingLink } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Loading } from "../../components/ui";
import { deviceTimeZone, formatMoment } from "../../time";
import { orderedFields, sharedValue } from "../clients/ProfileLinkCard";

/** Requests of companies to link their card with this profile and the links it has (§ 6.2.5,
 * § 6.3.8). Before accepting the recipient chooses which data to give; nothing else of the
 * profile is opened (§ 6.2.5). `base` is `/profile-links` for a person or
 * `/c/<CMP>/incoming-links` for a company. */
export function LinkRequests({
  base,
  title,
  hideEmpty,
  pendingOnly,
}: {
  base: string;
  title: string;
  hideEmpty?: boolean;
  /** Only the requests waiting for an answer: the cabinet lists the linked companies itself. */
  pendingOnly?: boolean;
}) {
  const { t } = useTranslation();
  const links = useQuery({
    queryKey: ["incoming-links", base],
    queryFn: async () => (await api<{ links: IncomingLink[] }>(base)).links,
  });
  if (links.isLoading) return <Loading />;
  const items = links.data ?? [];
  const pending = items.filter((l) => l.state === "pending");
  const linked = pendingOnly ? [] : items.filter((l) => l.state === "accepted" || l.state === "broken");
  if (!pending.length && !linked.length && hideEmpty) return null;
  return (
    <>
      <h2>{title}</h2>
      {!pending.length && !linked.length ? <p className="muted">{t("links.none_incoming")}</p> : null}
      {pending.map((link) => (
        <RequestCard key={link.id} base={base} link={link} />
      ))}
      {linked.length ? (
        <Card title={t("links.linked_companies")}>
          {linked.map((link) => (
            <p key={link.id}>
              <strong>{link.company.display_name}</strong>{" "}
              <span className="muted small">{link.company.public_id}</span>{" "}
              <span className={`badge ${link.state === "accepted" ? "ok" : ""}`}>{t(`links.state_${link.state}`)}</span>
            </p>
          ))}
        </Card>
      ) : null}
    </>
  );
}

function RequestCard({ base, link }: { base: string; link: IncomingLink }) {
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const offered = orderedFields(link.available).filter(([, value]) => value !== "" && value !== null);
  const [chosen, setChosen] = useState<Set<string>>(
    () => new Set(offered.map(([field]) => field).filter((f) => ["first_name", "last_name", "display_name"].includes(f))),
  );
  const accept = useCommand<{ fields: string[] }, unknown>(`${base}/${link.id}/accept`);
  const decline = useCommand<Record<string, never>, unknown>(`${base}/${link.id}/decline`);

  async function done() {
    await client.invalidateQueries({ queryKey: ["incoming-links", base] });
    await client.invalidateQueries({ queryKey: ["notifications"] });
  }

  function toggle(field: string) {
    const next = new Set(chosen);
    if (next.has(field)) next.delete(field);
    else next.add(field);
    setChosen(next);
  }

  return (
    <Card title={link.company.display_name}>
      <p>{t(link.card_type === "client" ? "links.asks_client" : "links.asks_counterparty")}</p>
      <p className="muted small">
        {link.company.public_id} ·{" "}
        {t("links.expires_at", { at: formatMoment(link.expires_at, deviceTimeZone(), i18n.language) })}
      </p>
      <p className="small">{t("links.choose_data")}</p>
      {offered.map(([field, value]) => (
        <div key={field}>
          <label className="small">
            <input type="checkbox" checked={chosen.has(field)} onChange={() => toggle(field)} />{" "}
            {t(`links.field_${field}`)}: <span className="muted">{sharedValue(value)}</span>
          </label>
        </div>
      ))}
      <p className="muted small">{t("links.consent")}</p>
      <ErrorMessage error={accept.error ?? decline.error} />
      <div className="actions">
        <button
          type="button"
          disabled={accept.pending}
          onClick={async () => {
            if ((await accept.run({ fields: [...chosen] })) !== null) await done();
          }}
        >
          {t("links.accept")}
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
