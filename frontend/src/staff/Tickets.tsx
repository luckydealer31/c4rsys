import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router";

import type { ApiError } from "../api/client";
import { useApi } from "../api/queries";
import { useCommand } from "../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../components/ui";
import { useWhen } from "./common";

const STATES = ["new", "in_progress", "waiting", "closed"] as const;

type Ticket = {
  id: string;
  number: number;
  received_on: string;
  channel: string;
  applicant_name: string;
  applicant_contact: string;
  subject: string;
  state: (typeof STATES)[number];
  assignee: { id: string; name: string } | null;
  created_at: string;
  closed_at: string | null;
  version: number;
  description?: string;
  result?: string;
  links?: {
    id: string;
    kind: "company" | "user" | "record";
    company?: { public_id: string; display_name: string } | null;
    user?: { public_id: string; name: string } | null;
    record?: { type: string; ref: string };
  }[];
  comments?: { id: string; kind: "comment" | "confirmation"; text: string; author: string; created_at: string }[];
};

type Member = { id: string; user: { public_id: string; name: string }; state: string };

/** § 24.1.3, § 24.1.7: tickets registered by hand from a message of an external channel. */
export function TicketsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [state, setState] = useState("");
  const [creating, setCreating] = useState(false);
  const list = useApi<{ tickets: Ticket[] }>(["staff", "tickets", state], `/staff/tickets${state ? `?state=${state}` : ""}`);
  const [form, setForm] = useState({ received_on: new Date().toISOString().slice(0, 10), applicant_name: "", applicant_contact: "", subject: "", description: "" });
  const create = useCommand<typeof form, { ticket: Ticket }>("/staff/tickets");
  return (
    <div className="page">
      <h1>{t("staff.nav_tickets")}</h1>
      <div className="actions">
        <select value={state} onChange={(e) => setState(e.target.value)}>
          <option value="">{t("staff.ticket_all")}</option>
          {STATES.map((s) => (
            <option key={s} value={s}>
              {t(`staff.ticket_state_${s}`)}
            </option>
          ))}
        </select>
        <button type="button" onClick={() => setCreating(true)}>
          {t("staff.ticket_new")}
        </button>
      </div>
      {creating ? (
        <Card title={t("staff.ticket_new")}>
          <form
            className="stack"
            onSubmit={async (e) => {
              e.preventDefault();
              const result = await create.run(form);
              if (result) navigate(`/tickets/${result.ticket.id}`);
            }}
          >
            <div className="row">
              <Field label={t("staff.ticket_received_on")}>
                <input type="date" value={form.received_on} onChange={(e) => setForm({ ...form, received_on: e.target.value })} />
              </Field>
              <Field label={t("staff.ticket_applicant")}>
                <input value={form.applicant_name} onChange={(e) => setForm({ ...form, applicant_name: e.target.value })} />
              </Field>
              <Field label={t("staff.ticket_contact")}>
                <input value={form.applicant_contact} onChange={(e) => setForm({ ...form, applicant_contact: e.target.value })} />
              </Field>
            </div>
            <Field label={t("staff.ticket_subject")}>
              <input value={form.subject} onChange={(e) => setForm({ ...form, subject: e.target.value })} />
            </Field>
            <Field label={t("staff.ticket_description")}>
              <textarea rows={4} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
            </Field>
            <ErrorMessage error={create.error} />
            <div className="actions">
              <button type="submit" disabled={create.pending || !form.subject.trim() || !form.description.trim()}>
                {t("staff.ticket_create")}
              </button>
              <button type="button" className="secondary" onClick={() => setCreating(false)}>
                {t("common.cancel")}
              </button>
            </div>
          </form>
        </Card>
      ) : null}
      {list.isLoading ? <Loading /> : null}
      <ErrorMessage error={list.error as ApiError | null} />
      <div className="stack">
        {list.data?.tickets.map((x) => (
          <Link key={x.id} className="list-link" to={`/tickets/${x.id}`}>
            <div className="list-link-head">
              <strong>
                № {x.number} · {x.subject}
              </strong>
              <span className="badge">{t(`staff.ticket_state_${x.state}`)}</span>
            </div>
            <div className="muted small">
              {x.received_on} · {x.applicant_name || "—"} {x.assignee ? `· ${x.assignee.name}` : ""}
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}

export function TicketPage() {
  const { t } = useTranslation();
  const { id = "" } = useParams();
  const when = useWhen();
  const client = useQueryClient();
  const found = useApi<{ ticket: Ticket }>(["staff", "ticket", id], `/staff/tickets/${id}`);
  const members = useApi<{ members: Member[] }>(["staff", "members"], "/staff/members");
  const [result, setResult] = useState("");
  const [comment, setComment] = useState("");
  const [kind, setKind] = useState<"comment" | "confirmation">("comment");
  const [link, setLink] = useState({ kind: "company", target: "", record_type: "" });
  const state = useCommand<{ state: string; result: string; version: number }, { ticket: Ticket }>(`/staff/tickets/${id}/state`);
  const assign = useCommand<{ member_id: string | null; version: number }, { ticket: Ticket }>(`/staff/tickets/${id}/assign`);
  const commentCmd = useCommand<{ kind: string; text: string }, { ticket: Ticket }>(`/staff/tickets/${id}/comments`);
  const linkCmd = useCommand<typeof link, { ticket: Ticket }>(`/staff/tickets/${id}/links`);
  const update = (data: { ticket: Ticket } | null) => {
    if (data) client.setQueryData(["staff", "ticket", id], data);
    return Boolean(data);
  };
  if (found.isLoading) return <Loading />;
  if (!found.data) return <ErrorMessage error={found.error as ApiError | null} />;
  const x = found.data.ticket;
  const next = STATES.filter((s) => s !== x.state && !(x.state === "closed" && s !== "in_progress") && s !== "new");
  return (
    <div className="page">
      <h1>
        № {x.number} · {x.subject}
      </h1>
      <p className="muted">
        {t(`staff.ticket_state_${x.state}`)} · {x.received_on} · {x.channel} · {x.applicant_name} {x.applicant_contact}
      </p>
      <Card title={t("staff.ticket_description")}>
        <p style={{ whiteSpace: "pre-wrap" }}>{x.description}</p>
        {x.result ? (
          <p>
            <strong>{t("staff.ticket_result")}:</strong> {x.result}
          </p>
        ) : null}
      </Card>
      <Card title={t("staff.ticket_work")}>
        <div className="row">
          <Field label={t("staff.ticket_assignee")}>
            <select
              value={x.assignee?.id ?? ""}
              onChange={async (e) => update(await assign.run({ member_id: e.target.value || null, version: x.version }))}
            >
              <option value="">—</option>
              {members.data?.members
                .filter((m) => m.state === "active")
                .map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.user.name || m.user.public_id}
                  </option>
                ))}
            </select>
          </Field>
          <Field label={t("staff.ticket_result")}>
            <input value={result} onChange={(e) => setResult(e.target.value)} />
          </Field>
        </div>
        <div className="actions">
          {next.map((s) => (
            <button
              key={s}
              type="button"
              className={s === "closed" ? "" : "secondary"}
              disabled={state.pending}
              onClick={async () => {
                if (update(await state.run({ state: s, result, version: x.version }))) setResult("");
              }}
            >
              {t(`staff.ticket_to_${s}`)}
            </button>
          ))}
        </div>
        <ErrorMessage error={state.error ?? assign.error} />
      </Card>
      <Card title={t("staff.ticket_links")}>
        {x.links?.map((l) => (
          <div key={l.id} className="small">
            {l.kind === "company" && l.company ? <Link to={`/companies/${l.company.public_id}`}>{l.company.display_name}</Link> : null}
            {l.kind === "user" && l.user ? <Link to={`/users/${l.user.public_id}`}>{l.user.name || l.user.public_id}</Link> : null}
            {l.kind === "record" && l.record ? `${l.record.type}: ${l.record.ref}` : null}
          </div>
        ))}
        <form
          className="row"
          onSubmit={async (e) => {
            e.preventDefault();
            if (update(await linkCmd.run(link))) setLink({ ...link, target: "", record_type: "" });
          }}
        >
          <select value={link.kind} onChange={(e) => setLink({ ...link, kind: e.target.value })}>
            <option value="company">{t("staff.target_company")}</option>
            <option value="user">{t("staff.target_user")}</option>
            <option value="record">{t("staff.target_record")}</option>
          </select>
          {link.kind === "record" ? (
            <input value={link.record_type} placeholder={t("staff.record_type")} onChange={(e) => setLink({ ...link, record_type: e.target.value })} />
          ) : null}
          <input value={link.target} placeholder={link.kind === "record" ? t("staff.record_ref") : link.kind === "company" ? "CMP-…" : "USR-…"} onChange={(e) => setLink({ ...link, target: e.target.value })} />
          <div className="actions" style={{ marginTop: 0, flex: "0 0 auto" }}>
            <button type="submit" disabled={linkCmd.pending || !link.target.trim()}>
              {t("staff.ticket_link")}
            </button>
          </div>
        </form>
        <ErrorMessage error={linkCmd.error} />
      </Card>
      <Card title={t("staff.ticket_comments")}>
        <div className="stack">
          {x.comments?.map((c) => (
            <div key={c.id} className="list-link">
              <div className="muted small">
                {when(c.created_at)} · {c.author} · {t(`staff.comment_${c.kind}`)}
              </div>
              <div style={{ whiteSpace: "pre-wrap" }}>{c.text}</div>
            </div>
          ))}
        </div>
        <form
          className="stack"
          onSubmit={async (e) => {
            e.preventDefault();
            if (update(await commentCmd.run({ kind, text: comment }))) setComment("");
          }}
        >
          <textarea rows={2} value={comment} onChange={(e) => setComment(e.target.value)} />
          <div className="actions">
            <select value={kind} onChange={(e) => setKind(e.target.value as "comment" | "confirmation")}>
              <option value="comment">{t("staff.comment_comment")}</option>
              <option value="confirmation">{t("staff.comment_confirmation")}</option>
            </select>
            <button type="submit" disabled={commentCmd.pending || !comment.trim()}>
              {t("staff.comment_add")}
            </button>
          </div>
          <ErrorMessage error={commentCmd.error} />
        </form>
      </Card>
    </div>
  );
}
