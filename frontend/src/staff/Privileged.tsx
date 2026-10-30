import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";

import type { ApiError } from "../api/client";
import { useApi } from "../api/queries";
import { useCommand } from "../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../components/ui";
import { ReasonForm, useRefreshStaff, useStaff, useStaffCan, useWhen, type PrivilegedSession } from "./common";

const SCOPE_PERMISSION: Record<string, string> = {
  finance: "privileged.finance",
  documents: "privileged.documents",
  files: "privileged.files",
};

/** § 24.3.1: a privileged session is opened for one company or user with a reason, a ticket if
 * there is one, and the areas of data; it lasts 1 hour (В-11.3). */
export function SessionsPage() {
  const { t } = useTranslation();
  const when = useWhen();
  const me = useStaff();
  const can = useStaffCan();
  const navigate = useNavigate();
  const refresh = useRefreshStaff();
  const [params] = useSearchParams();
  const [targetType, setTargetType] = useState(params.get("type") === "user" ? "user" : "company");
  const [target, setTarget] = useState(params.get("target") ?? "");
  const [category, setCategory] = useState(me.catalog.session_reasons[0] ?? "");
  const [reason, setReason] = useState("");
  const [ticket, setTicket] = useState("");
  const [scopes, setScopes] = useState<string[]>([]);
  const list = useApi<{ sessions: PrivilegedSession[] }>(["staff", "sessions"], "/staff/privileged");
  const open = useCommand<Record<string, unknown>, { session: PrivilegedSession }>("/staff/privileged");
  const optional = me.catalog.scopes.filter((s) => s !== "view");
  return (
    <div className="page">
      <h1>{t("staff.nav_sessions")}</h1>
      {me.privileged_session ? null : (
        <Card title={t("staff.open_session")}>
          <form
            className="stack"
            onSubmit={async (event) => {
              event.preventDefault();
              const result = await open.run({
                target_type: targetType,
                target: target.trim(),
                reason_category: category,
                reason,
                ticket_number: ticket ? Number(ticket) : null,
                scopes,
              });
              if (result) {
                refresh();
                navigate(`/sessions/${result.session.id}`);
              }
            }}
          >
            <div className="row">
              <Field label={t("staff.target_type")}>
                <select value={targetType} onChange={(e) => setTargetType(e.target.value)}>
                  <option value="company">{t("staff.target_company")}</option>
                  <option value="user">{t("staff.target_user")}</option>
                </select>
              </Field>
              <Field label={t("staff.target_id")}>
                <input value={target} onChange={(e) => setTarget(e.target.value)} placeholder={targetType === "company" ? "CMP-…" : "USR-…"} />
              </Field>
            </div>
            <div className="row">
              <Field label={t("staff.reason_category")}>
                <select value={category} onChange={(e) => setCategory(e.target.value)}>
                  {me.catalog.session_reasons.map((r) => (
                    <option key={r} value={r}>
                      {t(`staff.reason_${r}`)}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label={t("staff.ticket_number")}>
                <input inputMode="numeric" value={ticket} onChange={(e) => setTicket(e.target.value.replace(/\D/g, ""))} />
              </Field>
            </div>
            <Field label={t("staff.session_reason")}>
              <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={2} />
            </Field>
            <fieldset className="stack">
              <legend>{t("staff.scopes")}</legend>
              <label className="check">
                <input type="checkbox" checked disabled /> {t("staff.scope_view")}
              </label>
              {optional.map((scope) => (
                <label key={scope} className="check">
                  <input
                    type="checkbox"
                    disabled={!can(SCOPE_PERMISSION[scope] ?? "")}
                    checked={scopes.includes(scope)}
                    onChange={(e) => setScopes(e.target.checked ? [...scopes, scope] : scopes.filter((x) => x !== scope))}
                  />{" "}
                  {t(`staff.scope_${scope}`)}
                </label>
              ))}
            </fieldset>
            <ErrorMessage error={open.error} />
            <div className="actions">
              <button type="submit" disabled={open.pending || !target.trim() || !reason.trim()}>
                {t("staff.open_session")}
              </button>
            </div>
          </form>
        </Card>
      )}
      <h2>{t("staff.my_sessions")}</h2>
      {list.isLoading ? <Loading /> : null}
      <div className="stack">
        {list.data?.sessions.map((s) => (
          <Link key={s.id} className="list-link" to={`/sessions/${s.id}`}>
            <div className="list-link-head">
              <strong>{s.company?.display_name ?? s.user?.name}</strong>
              {s.active ? <span className="badge warn">{t("staff.session_active")}</span> : <span className="badge">{t("staff.session_ended")}</span>}
            </div>
            <div className="muted small">
              {when(s.started_at)} · {t(`staff.reason_${s.reason_category}`)} · {s.scopes.map((x) => t(`staff.scope_${x}`)).join(", ")}
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}

type Item = Record<string, unknown>;

function label(item: Item): string {
  const person = (item.user as Item | null | undefined) ?? item;
  const name = [person.last_name, person.first_name].filter((x) => typeof x === "string" && x).join(" ");
  for (const value of [item.title, item.name, item.display_name, name, item.company_name]) {
    if (typeof value === "string" && value) return value;
  }
  if (typeof item.number === "number") return `№ ${item.number}`;
  return String(item.id ?? "");
}

/** A read-only view of any record: its plain fields; the whole record on demand. */
function Record_({ item }: { item: Item }) {
  const { t } = useTranslation();
  const plain = Object.entries(item).filter(([, v]) => v === null || ["string", "number", "boolean"].includes(typeof v));
  return (
    <div>
      <dl className="facts">
        {plain.map(([k, v]) => (
          <div key={k}>
            <dt>{k}</dt>
            <dd>{v === null || v === "" ? "—" : String(v)}</dd>
          </div>
        ))}
      </dl>
      <details>
        <summary className="muted small">{t("staff.all_fields")}</summary>
        <pre className="small staff-json">{JSON.stringify(item, null, 2)}</pre>
      </details>
    </div>
  );
}

const COMPANY_SECTIONS: [key: string, path: string, list: string, scope: string][] = [
  ["company_profile", "company", "company", "view"],
  ["employees", "employees", "employees", "view"],
  ["vehicles", "vehicles", "vehicles", "view"],
  ["clients", "clients", "clients", "view"],
  ["orders", "orders", "orders", "view"],
  ["rentals", "rentals", "rentals", "view"],
  ["documents", "documents", "documents", "documents"],
  ["finance", "finance", "cash_accounts", "finance"],
];

function SectionView({ session, section }: { session: PrivilegedSession; section: (typeof COMPANY_SECTIONS)[number] }) {
  const { t } = useTranslation();
  const [key, path, list] = section;
  const [detail, setDetail] = useState<string | null>(null);
  const base = `/staff/privileged/${session.id}`;
  const data = useApi<Record<string, unknown>>(["staff", "pv", session.id, path], `${base}/${path}`);
  const opens = key === "orders" || key === "rentals";
  const one = useApi<Record<string, Item>>(["staff", "pv", session.id, path, detail], `${base}/${path}/${detail}`, opens && detail !== null);
  if (data.isLoading) return <Loading />;
  if (data.error) return <ErrorMessage error={data.error as ApiError} />;
  const body = data.data?.[list];
  if (!Array.isArray(body)) return body ? <Record_ item={body as Item} /> : null;
  const items = body as Item[];
  return (
    <div className="stack">
      {items.length === 0 ? <p className="muted">{t("staff.empty")}</p> : null}
      {items.map((item) => {
        const itemId = String(item.id);
        const expanded = detail === itemId;
        return (
          <div key={itemId} className="list-link">
            <button type="button" className="link-button" aria-expanded={expanded} onClick={() => setDetail(expanded ? null : itemId)}>
              {expanded ? "▾" : "▸"} <strong>{label(item)}</strong>{" "}
              {typeof item.state === "string" ? <span className="badge">{item.state}</span> : null}
            </button>
            {expanded ? (
              opens ? (
                one.isLoading ? (
                  <Loading />
                ) : one.data ? (
                  <Record_ item={Object.values(one.data)[0] ?? {}} />
                ) : (
                  <ErrorMessage error={one.error as ApiError | null} />
                )
              ) : (
                <Record_ item={item} />
              )
            ) : null}
            {expanded && key === "documents" && session.scopes.includes("files") ? (
              <a className="button secondary" href={`/api${base}/documents/${itemId}/file`}>
                {t("staff.download")}
              </a>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function UserView({ session }: { session: PrivilegedSession }) {
  const data = useApi<{ user: Item }>(["staff", "pv", session.id, "user"], `/staff/privileged/${session.id}/user`);
  if (data.isLoading) return <Loading />;
  if (!data.data) return <ErrorMessage error={data.error as ApiError | null} />;
  return <Record_ item={data.data.user} />;
}

/** The read-only view of a privileged session: there is no action on the company's data here
 * (§ 24.1.4); every opened section is recorded by the server. */
export function SessionPage() {
  const { t } = useTranslation();
  const { id = "" } = useParams();
  const when = useWhen();
  const can = useStaffCan();
  const client = useQueryClient();
  const refresh = useRefreshStaff();
  const [section, setSection] = useState<string | null>(null);
  const [deferring, setDeferring] = useState(false);
  const found = useApi<{ session: PrivilegedSession }>(["staff", "session", id], `/staff/privileged/${id}`);
  const close = useCommand<Record<string, never>, { session: PrivilegedSession }>(`/staff/privileged/${id}/close`);
  const defer = useCommand<{ reason: string }, { session: PrivilegedSession }>(`/staff/privileged/${id}/notice/defer`);
  const release = useCommand<Record<string, never>, { session: PrivilegedSession }>(`/staff/privileged/${id}/notice/release`);
  const changed = () => {
    void client.invalidateQueries({ queryKey: ["staff", "session", id] });
    void client.invalidateQueries({ queryKey: ["staff", "sessions"] });
    refresh();
  };
  if (found.isLoading) return <Loading />;
  if (!found.data) return <ErrorMessage error={found.error as ApiError | null} />;
  const s = found.data.session;
  const sections = COMPANY_SECTIONS.filter(([, , , scope]) => s.scopes.includes(scope));
  const current = sections.find(([key]) => key === section);
  return (
    <div className="page wide">
      <h1>
        {t("staff.session_title")}: {s.company?.display_name ?? s.user?.name}
      </h1>
      <p className="muted">
        {t(`staff.reason_${s.reason_category}`)} · {s.reason}
        {s.ticket_number ? ` · ${t("staff.ticket")} № ${s.ticket_number}` : ""}
      </p>
      <p className="muted small">
        {when(s.started_at)} — {s.ended_at ? when(s.ended_at) : `${t("staff.until")} ${when(s.expires_at)}`} ·{" "}
        {s.scopes.map((x) => t(`staff.scope_${x}`)).join(", ")} · {t(`staff.notice_${s.notice}`)}
      </p>
      <div className="actions">
        {s.ended_at === null ? (
          <button
            type="button"
            className="danger"
            disabled={close.pending}
            onClick={async () => {
              if (await close.run({})) changed();
            }}
          >
            {t("staff.close_session")}
          </button>
        ) : null}
        {can("privileged.notice_defer") && s.notice === "pending" && s.target_type === "company" ? (
          <button type="button" className="secondary" onClick={() => setDeferring(true)}>
            {t("staff.notice_defer")}
          </button>
        ) : null}
        {can("privileged.notice_defer") && s.notice === "deferred" ? (
          <button
            type="button"
            className="secondary"
            onClick={async () => {
              if (await release.run({})) changed();
            }}
          >
            {t("staff.notice_release")}
          </button>
        ) : null}
      </div>
      <ErrorMessage error={close.error ?? release.error} />
      {deferring ? (
        <ReasonForm
          label={t("staff.notice_defer_reason")}
          submit={t("staff.notice_defer")}
          pending={defer.pending}
          error={defer.error}
          onCancel={() => setDeferring(false)}
          onSubmit={async (reason) => {
            if (await defer.run({ reason })) {
              setDeferring(false);
              changed();
            }
          }}
        />
      ) : null}
      {s.active ? (
        s.target_type === "user" ? (
          <Card title={t("staff.section_user_profile")}>
            <UserView session={s} />
          </Card>
        ) : (
          <>
            <div className="tabs staff-tabs">
              {sections.map(([key]) => (
                <button key={key} type="button" className={key === section ? "active" : ""} onClick={() => setSection(key)}>
                  {t(`staff.section_${key}`)}
                </button>
              ))}
            </div>
            {current ? <SectionView key={current[0]} session={s} section={current} /> : <p className="muted">{t("staff.choose_section")}</p>}
          </>
        )
      ) : (
        <div className="alert warn">{s.ended_at ? t("staff.session_ended_hint") : t("staff.banner_expired")}</div>
      )}
      {s.sections.length ? (
        <p className="muted small">
          {t("staff.viewed_sections")}: {s.sections.map((x) => t(`staff.section_${x}`)).join(", ")}
        </p>
      ) : null}
    </div>
  );
}
