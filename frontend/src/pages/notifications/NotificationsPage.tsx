import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useLocation } from "react-router";

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
  /** § 20.1.5: the context of the location, when applicable. */
  context?: { id: string; type: string } | null;
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
  /** A recipient by the organisation structure without access to the object (§ 5.2.3). */
  limited?: boolean;
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
  employment: "/clients/employees/",
  warehouse_inventory: "/warehouse/inventories/",
  warehouse_operation: "/warehouse/operations/",
  warehouse_item: "/warehouse/items/",
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
  // Inside a company the centre opens inside it: the menu of the company stays (decision of
  // 16.11.2026). The number counts the unread of every company, so nothing is missed.
  const inside = /^\/c\/([^/]+)/.exec(useLocation().pathname)?.[1];
  return (
    <Link to={inside ? `/c/${inside}/notifications` : "/notifications"} className="bell" aria-label={t("notifications.title")} data-tip={t("notifications.title")}>
      <Icon name="bell" size={22} />
      {unread ? <span className="bell-count">{unread > 99 ? "99+" : unread}</span> : null}
    </Link>
  );
}

export function notificationText(t: (key: string, options?: Record<string, unknown>) => string, n: CentreNotification): string {
  const event = n.event.replaceAll(".", "_");
  if (n.limited) {
    // Only the employee and the fact (§ 5.2.3).
    return t(`notifications.event_${event}_limited`, { ...n.params, defaultValue: n.event });
  }
  if (n.event === "task.overdue" && n.params.assignee_name) {
    return t("notifications.event_task_overdue_of", { ...n.params });
  }
  return t(`notifications.event_${event}`, { ...n.params, defaultValue: n.event });
}

/** The notification centre of the user: every company, with filters (§ 20.1.5). Opened inside
 * a company (`current`) it shows that company, with a switch to all of them (decision of
 * 16.11.2026). */
export function NotificationsPage({ current = "" }: { current?: string }) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const [kind, setKind] = useState("");
  const [state, setState] = useState("active");
  const [scope, setScope] = useState<"this" | "all">(current ? "this" : "all");
  const [chosenCompany, setCompany] = useState("");
  const company = scope === "this" ? current : chosenCompany;
  const [location, setLocation] = useState<{ id: string; name: string } | null>(null);
  const [contextType, setContextType] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [unread, setUnread] = useState(false);
  const companies = useQuery({
    queryKey: ["companies"],
    queryFn: async () => (await api<{ companies: CompanySummary[] }>("/companies")).companies,
  });
  const several = (companies.data ?? []).length > 1;
  const query = [
    kind && `kind=${kind}`,
    state && `state=${state}`,
    company && `company=${company}`,
    location && `location=${location.id}`,
    contextType && `context_type=${contextType}`,
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
  // The locations of the notifications shown: the centre is the user's own and covers companies
  // where he may not see the list of locations.
  const places = new Map(items.flatMap((n) => (n.location ? [[n.location.id, n.location.name] as const] : [])));
  if (location) places.set(location.id, location.name);

  return (
    <div className="page">
      <h1>{t("notifications.title")}</h1>
      {/* With one company there is nothing to switch between: only the list. */}
      {current && several ? (
        <div className="segmented" role="group" style={{ marginBottom: 16 }}>
          {(["this", "all"] as const).map((key) => (
            <button key={key} type="button" aria-pressed={scope === key} onClick={() => setScope(key)}>
              {t(`notifications.scope_${key}`)}
            </button>
          ))}
        </div>
      ) : null}
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
        {scope === "all" ? (
          <Field label={t("notifications.company")}>
            <select value={chosenCompany} onChange={(e) => setCompany(e.target.value)}>
              <option value="">{t("notifications.all")}</option>
              {(companies.data ?? []).map((c) => (
                <option key={c.public_id} value={c.public_id}>
                  {c.display_name}
                </option>
              ))}
            </select>
          </Field>
        ) : null}
        <Field label={t("notifications.location")}>
          <select
            value={location?.id ?? ""}
            onChange={(e) => setLocation(e.target.value ? { id: e.target.value, name: places.get(e.target.value) ?? "" } : null)}
          >
            <option value="">{t("notifications.all")}</option>
            {[...places.entries()].map(([id, name]) => (
              <option key={id} value={id}>
                {name}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("notifications.context")}>
          <select value={contextType} onChange={(e) => setContextType(e.target.value)}>
            <option value="">{t("notifications.all")}</option>
            {(["rental", "service", "warehouse"] as const).map((c) => (
              <option key={c} value={c}>
                {t(`contexts.${c}`)}
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
        <button type="button" className="secondary" disabled={read.pending} onClick={() => void read.run({ all: true, ...(scope === "this" ? { company: current } : {}) }).then((r) => (r ? refresh() : undefined))}>
          {t(scope === "this" && several ? "notifications.read_all_here" : "notifications.read_all")}
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
                {current && n.company.public_id !== current ? (
                  <span className="badge">{t("notifications.other_company", { name: n.company.name })}</span>
                ) : (
                  n.company.name
                )}
                {n.location ? ` · ${n.location.name}` : ""}
                {n.context ? ` · ${t(`contexts.${n.context.type}`)}` : ""} · {when(n.last_at)}
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
