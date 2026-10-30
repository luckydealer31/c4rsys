import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { api } from "../../api/client";
import { useApi } from "../../api/queries";
import type { CompanySummary } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { Icon } from "../../components/icons";
import { deviceTimeZone, formatMoment } from "../../time";

export type CentreNotification = {
  id: string;
  company: { public_id: string; name: string };
  location: { id: string; name: string } | null;
  kind: "action" | "warning" | "reminder" | "info";
  event: string;
  params: Record<string, string | number | null>;
  object_type: string | null;
  object_id: string | null;
  state: "active" | "done";
  created_at: string;
  last_at: string;
  repeats: number;
  read: boolean;
};

const OBJECT_PATHS: Record<string, string> = {
  task: "/tasks/",
  document: "/documents/",
  rental: "/rentals/",
  order: "/orders/",
  vehicle: "/vehicles/",
  client: "/clients/",
  counterparty: "/clients/counterparties/",
  profile_link: "/link-requests/",
  owner_request: "/link-requests/",
  service_operation: "/service/operations/",
  delivery: "/trips/",
};

/** The mark in the header: unread active notifications (§ 20.1.5; З3). */
export function NotificationBell() {
  const { t } = useTranslation();
  const count = useQuery({
    queryKey: ["notifications", "count"],
    queryFn: () => api<{ unread: number }>("/notifications/count"),
    refetchInterval: 60_000,
  });
  const unread = count.data?.unread ?? 0;
  return (
    <Link to="/notifications" className="bell" aria-label={t("notifications.title")} data-tip={t("notifications.title")}>
      <Icon name="bell" size={22} />
      {unread ? <span className="bell-count">{unread > 99 ? "99+" : unread}</span> : null}
    </Link>
  );
}

export function notificationText(t: (key: string, options?: Record<string, unknown>) => string, n: CentreNotification): string {
  return t(`notifications.event_${n.event.replaceAll(".", "_")}`, { ...n.params, defaultValue: n.event });
}

/** The notification centre of the user: every company, with filters (§ 20.1.5). */
export function NotificationsPage() {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const [kind, setKind] = useState("");
  const [state, setState] = useState("active");
  const [company, setCompany] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [unread, setUnread] = useState(false);
  const companies = useQuery({
    queryKey: ["companies"],
    queryFn: async () => (await api<{ companies: CompanySummary[] }>("/companies")).companies,
  });
  const query = [
    kind && `kind=${kind}`,
    state && `state=${state}`,
    company && `company=${company}`,
    from && `from=${from}`,
    to && `to=${to}`,
    unread && "unread=1",
  ]
    .filter(Boolean)
    .join("&");
  const list = useApi<{ notifications: CentreNotification[] }>(["notifications", query], `/notifications?${query}`);
  const read = useCommand<Record<string, unknown>, { marked: number }>("/notifications/read");
  const zone = deviceTimeZone();
  const when = (value: string) => formatMoment(value, zone, i18n.language);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["notifications"] });
  const items = list.data?.notifications ?? [];

  return (
    <div className="page">
      <h1>{t("notifications.title")}</h1>
      <div className="row">
        <Field label={t("notifications.kind")}>
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="">{t("notifications.all")}</option>
            {(["action", "warning", "reminder", "info"] as const).map((k) => (
              <option key={k} value={k}>
                {t(`notifications.kind_${k}`)}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("notifications.state")}>
          <select value={state} onChange={(e) => setState(e.target.value)}>
            <option value="">{t("notifications.all")}</option>
            <option value="active">{t("notifications.state_active")}</option>
            <option value="done">{t("notifications.state_done")}</option>
          </select>
        </Field>
        <Field label={t("notifications.company")}>
          <select value={company} onChange={(e) => setCompany(e.target.value)}>
            <option value="">{t("notifications.all")}</option>
            {(companies.data ?? []).map((c) => (
              <option key={c.public_id} value={c.public_id}>
                {c.display_name}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("notifications.from")}>
          <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
        </Field>
        <Field label={t("notifications.to")}>
          <input type="date" value={to} onChange={(e) => setTo(e.target.value)} />
        </Field>
      </div>
      <label className="small">
        <input type="checkbox" checked={unread} onChange={() => setUnread(!unread)} /> {t("notifications.only_unread")}
      </label>
      <div className="actions">
        <button type="button" className="secondary" disabled={read.pending} onClick={() => void read.run({ all: true }).then((r) => (r ? refresh() : undefined))}>
          {t("notifications.read_all")}
        </button>
      </div>
      <ErrorMessage error={read.error} />
      <Card>
        {list.isLoading ? <Loading /> : null}
        {list.data && !items.length ? <p className="muted">{t("notifications.none")}</p> : null}
        {items.map((n) => {
          const path = n.object_type && n.object_id && OBJECT_PATHS[n.object_type];
          const text = notificationText(t, n);
          return (
            <div key={n.id} className="small" style={{ padding: "8px 0", borderBottom: "1px solid var(--border, #ddd)", fontWeight: n.read ? 400 : 600 }}>
              <span className={`badge ${n.kind === "warning" || n.kind === "action" ? "warn" : ""}`}>{t(`notifications.kind_${n.kind}`)}</span>{" "}
              {path ? <Link to={`/c/${n.company.public_id}${path}${n.object_id}`}>{text}</Link> : text}
              <div className="muted" style={{ fontWeight: 400 }}>
                {n.company.name}
                {n.location ? ` · ${n.location.name}` : ""} · {when(n.last_at)}
                {n.repeats ? ` · ${t("notifications.repeats", { count: n.repeats })}` : ""}
                {n.state === "done" ? ` · ${t("notifications.state_done")}` : ""}{" "}
                {!n.read ? (
                  <button type="button" className="link" disabled={read.pending} onClick={() => void read.run({ ids: [n.id] }).then((r) => (r ? refresh() : undefined))}>
                    {t("notifications.mark_read")}
                  </button>
                ) : null}
              </div>
            </div>
          );
        })}
      </Card>
    </div>
  );
}
