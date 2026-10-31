import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router";

import type { ApiError } from "../api/client";
import { useApi } from "../api/queries";
import { useCommand } from "../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../components/ui";
import { ReasonForm, useWhen } from "./common";

export type UserLink = {
  module: string;
  kind: string;
  record_id: string;
  label: Record<string, unknown>;
  conflict_key?: string | null;
  blocking?: boolean;
  assigned_to?: "main" | "secondary" | null;
};
type Conflict = { account: string; key: string; links: (UserLink & { side: string })[] };
type Person = { public_id: string; name: string; state?: string; email_masked?: string };
type Preview = { main: Person; secondary: Person; moving: UserLink[]; kept: UserLink[]; conflicts: Conflict[] };
type Merge = {
  id: string;
  state: "merged" | "splitting" | "split";
  main: { public_id: string; name: string };
  secondary: { public_id: string; name: string };
  reason: string;
  merged_at: string;
  split_reason: string;
  split_at: string | null;
  version: number;
  history?: (UserLink & { phase: "moved" | "kept" })[];
  split?: {
    restored: UserLink[];
    staying: UserLink[];
    new: UserLink[];
    gone: { module: string; kind: string; record_id: string }[];
    unassigned: number;
    conflicts: Conflict[];
  };
};

/** A link described for the support: its kind and the readable label, no closed data. */
export function LinkLabel({ link }: { link: UserLink }) {
  const { t } = useTranslation();
  const label = link.label;
  const parts = [
    label.company_name as string | undefined,
    label.company as string | undefined,
    label.vehicle as string | undefined,
    label.card as string | undefined,
    label.value as string | undefined,
    label.percent ? `${String(label.percent)}%` : undefined,
    label.state ? t(`staff.link_state_${String(label.state)}`, { defaultValue: String(label.state) }) : undefined,
  ].filter(Boolean);
  return (
    <span>
      <strong>{t(`staff.link_kind_${link.kind}`, { defaultValue: link.kind })}</strong>
      {parts.length ? ` · ${parts.join(" · ")}` : ""}
    </span>
  );
}

function Conflicts({ conflicts }: { conflicts: Conflict[] }) {
  const { t } = useTranslation();
  if (!conflicts.length) return <p className="muted">{t("staff.merge_no_conflicts")}</p>;
  return (
    <div className="alert error" role="alert">
      <strong>{t("staff.merge_conflicts")}</strong>
      <ul>
        {conflicts.map((c, index) => (
          <li key={`${c.key}-${index}`}>
            {t(`staff.conflict_${c.key.split(":")[0]}`, { defaultValue: c.key })}:{" "}
            {c.links.map((l) => (
              <span key={l.record_id} className="chip">
                {t(`staff.side_${l.side}`, { defaultValue: l.side })} — <LinkLabel link={l} />
              </span>
            ))}
          </li>
        ))}
      </ul>
      <p className="small">{t("staff.merge_conflicts_hint")}</p>
    </div>
  );
}

function LinkList({ title, links }: { title: string; links: UserLink[] }) {
  const { t } = useTranslation();
  return (
    <div>
      <h3>{title}</h3>
      {links.length ? (
        <ul className="plain">
          {links.map((l) => (
            <li key={`${l.module}-${l.kind}-${l.record_id}`}>
              <LinkLabel link={l} />
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">{t("staff.none")}</p>
      )}
    </div>
  );
}

/** § 24.1.8: the merge of two global accounts, only by the support and never automatically. */
export function MergesPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const when = useWhen();
  const list = useApi<{ merges: Merge[] }>(["staff", "merges"], "/staff/merges");
  const [main, setMain] = useState("");
  const [secondary, setSecondary] = useState("");
  const [reason, setReason] = useState("");
  const [ticket, setTicket] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const look = useCommand<{ main: string; secondary: string }, { preview: Preview }>("/staff/merges/preview");
  const merge = useCommand<Record<string, unknown>, { merge: Merge }>("/staff/merges");
  return (
    <div className="page">
      <h1>{t("staff.nav_merges")}</h1>
      <Card title={t("staff.merge_new")}>
        <form
          className="stack"
          onSubmit={async (e) => {
            e.preventDefault();
            const found = await look.run({ main: main.trim(), secondary: secondary.trim() });
            setPreview(found?.preview ?? null);
          }}
        >
          <div className="row">
            <Field label={t("staff.merge_main")} hint={t("staff.merge_main_hint")}>
              <input value={main} placeholder="USR-…" onChange={(e) => { setMain(e.target.value); setPreview(null); }} />
            </Field>
            <Field label={t("staff.merge_secondary")} hint={t("staff.merge_secondary_hint")}>
              <input value={secondary} placeholder="USR-…" onChange={(e) => { setSecondary(e.target.value); setPreview(null); }} />
            </Field>
          </div>
          <ErrorMessage error={look.error} />
          <div className="actions">
            <button type="submit" className="secondary" disabled={look.pending || !main.trim() || !secondary.trim()}>
              {t("staff.merge_preview")}
            </button>
          </div>
        </form>
        {preview ? (
          <div className="stack">
            <p>
              {t("staff.merge_summary", {
                secondary: `${preview.secondary.name} (${preview.secondary.public_id})`,
                main: `${preview.main.name} (${preview.main.public_id})`,
              })}
            </p>
            <div className="row">
              <LinkList title={t("staff.merge_moving")} links={preview.moving} />
              <LinkList title={t("staff.merge_kept")} links={preview.kept} />
            </div>
            <p className="muted small">{t("staff.merge_not_rewritten")}</p>
            <Conflicts conflicts={preview.conflicts} />
            {!preview.conflicts.length ? (
              <form
                className="stack"
                onSubmit={async (e) => {
                  e.preventDefault();
                  const done = await merge.run({
                    main: preview.main.public_id,
                    secondary: preview.secondary.public_id,
                    reason,
                    ...(ticket ? { ticket_number: Number(ticket) } : {}),
                    expected: preview.moving.map((l) => l.record_id),
                  });
                  if (done) navigate(`/merges/${done.merge.id}`);
                }}
              >
                <Field label={t("staff.merge_reason")} hint={t("staff.merge_reason_hint")}>
                  <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={2} />
                </Field>
                <Field label={t("staff.ticket_number")}>
                  <input inputMode="numeric" value={ticket} onChange={(e) => setTicket(e.target.value.replace(/\D/g, ""))} />
                </Field>
                <ErrorMessage error={merge.error} />
                <div className="actions">
                  <button type="submit" className="danger" disabled={merge.pending || !reason.trim()}>
                    {t("staff.merge_submit")}
                  </button>
                </div>
              </form>
            ) : null}
          </div>
        ) : null}
      </Card>
      <Card title={t("staff.merge_history")}>
        {list.isLoading ? <Loading /> : null}
        <ErrorMessage error={list.error as ApiError | null} />
        <div className="stack">
          {list.data?.merges.map((m) => (
            <Link key={m.id} to={`/merges/${m.id}`} className="list-link">
              <strong>
                {m.secondary.public_id} → {m.main.public_id}
              </strong>{" "}
              · {t(`staff.merge_state_${m.state}`)} · <span className="muted small">{when(m.merged_at)}</span>
            </Link>
          ))}
          {list.data && !list.data.merges.length ? <p className="muted">{t("staff.none")}</p> : null}
        </div>
      </Card>
    </div>
  );
}

/** A merge with its log of the transfer and the split of an erroneous merge (§ 24.1.8). */
export function MergePage() {
  const { t } = useTranslation();
  const { id = "" } = useParams();
  const when = useWhen();
  const client = useQueryClient();
  const key = ["staff", "merge", id];
  const found = useApi<{ merge: Merge }>(key, `/staff/merges/${id}`);
  const start = useCommand<{ reason: string; version: number }, { merge: Merge }>(`/staff/merges/${id}/split/start`);
  const assign = useCommand<Record<string, unknown>, { merge: Merge }>(`/staff/merges/${id}/split/assign`);
  const complete = useCommand<{ version: number }, { merge: Merge }>(`/staff/merges/${id}/split/complete`);
  const [splitting, setSplitting] = useState(false);
  const set = (merge: Merge | undefined) => {
    if (merge) client.setQueryData(key, { merge });
  };
  if (found.isLoading) return <Loading />;
  if (found.error || !found.data) return <ErrorMessage error={found.error as ApiError | null} />;
  const m = found.data.merge;
  return (
    <div className="page">
      <p>
        <Link to="/merges">← {t("staff.nav_merges")}</Link>
      </p>
      <h1>
        {m.secondary.name} ({m.secondary.public_id}) → {m.main.name} ({m.main.public_id})
      </h1>
      <p>
        {t(`staff.merge_state_${m.state}`)} · {when(m.merged_at)}
      </p>
      <Card title={t("staff.merge_reason")}>
        <p>{m.reason}</p>
      </Card>
      <Card title={t("staff.merge_log")}>
        <LinkList title={t("staff.merge_moved")} links={(m.history ?? []).filter((x) => x.phase === "moved")} />
        <LinkList title={t("staff.merge_kept_before")} links={(m.history ?? []).filter((x) => x.phase === "kept")} />
      </Card>
      {m.state === "merged" ? (
        splitting ? (
          <Card title={t("staff.split_start")}>
            <ReasonForm
              label={t("staff.split_reason")}
              submit={t("staff.split_start")}
              pending={start.pending}
              error={start.error}
              onSubmit={async (reason) => {
                set((await start.run({ reason, version: m.version }))?.merge);
                setSplitting(false);
              }}
              onCancel={() => setSplitting(false)}
            />
          </Card>
        ) : (
          <div className="actions">
            <button type="button" className="secondary" onClick={() => setSplitting(true)}>
              {t("staff.split_start")}
            </button>
          </div>
        )
      ) : null}
      {m.state === "split" ? (
        <Card title={t("staff.split_done")}>
          <p>{m.split_reason}</p>
          <p className="muted small">{when(m.split_at)}</p>
        </Card>
      ) : null}
      {m.split ? (
        <Card title={t("staff.split_title")}>
          <p className="muted">{m.split_reason}</p>
          <LinkList title={t("staff.split_restored")} links={m.split.restored} />
          <LinkList title={t("staff.split_staying")} links={m.split.staying} />
          <h3>{t("staff.split_new")}</h3>
          <p className="muted small">{t("staff.split_new_hint")}</p>
          {m.split.new.length ? (
            <ul className="plain">
              {m.split.new.map((l) => (
                <li key={l.record_id} className="row">
                  <LinkLabel link={l} />
                  {(["main", "secondary"] as const).map((target) => (
                    <button
                      key={target}
                      type="button"
                      className={l.assigned_to === target ? "" : "secondary"}
                      disabled={assign.pending}
                      onClick={async () =>
                        set(
                          (
                            await assign.run({
                              module: l.module,
                              kind: l.kind,
                              record_id: l.record_id,
                              target,
                              version: m.version,
                            })
                          )?.merge,
                        )
                      }
                    >
                      {target === "main" ? m.main.public_id : m.secondary.public_id}
                    </button>
                  ))}
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">{t("staff.none")}</p>
          )}
          <ErrorMessage error={assign.error} />
          <Conflicts conflicts={m.split.conflicts} />
          <ErrorMessage error={complete.error} />
          <div className="actions">
            <button
              type="button"
              className="danger"
              disabled={complete.pending || m.split.unassigned > 0 || m.split.conflicts.length > 0}
              onClick={async () => set((await complete.run({ version: m.version }))?.merge)}
            >
              {t("staff.split_complete")}
            </button>
            {m.split.unassigned > 0 ? (
              <span className="muted small">{t("staff.split_unassigned", { count: m.split.unassigned })}</span>
            ) : null}
          </div>
        </Card>
      ) : null}
    </div>
  );
}
