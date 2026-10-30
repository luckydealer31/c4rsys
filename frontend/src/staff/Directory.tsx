import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams, useSearchParams } from "react-router";

import type { ApiError } from "../api/client";
import { useApi } from "../api/queries";
import { useCommand } from "../api/useCommand";
import { Card, ErrorMessage, Loading } from "../components/ui";
import { BlockForm, ReasonForm, useStaffCan, useWhen, type Block } from "./common";

type UserSummary = {
  public_id: string;
  name: string;
  username: string | null;
  state: string;
  created_at: string;
  email_masked: string;
  blocked: boolean;
  is_staff: boolean;
};

type UserCardData = UserSummary & {
  contacts_masked: { login_emails: string[]; contact_email: string; contact_phone: string };
  companies: { public_id: string; display_name: string; state: string; top_authority: boolean }[];
  open_sessions: number;
  blocks: Block[];
};

type CompanySummary = {
  public_id: string;
  display_name: string;
  country: string;
  created_at: string;
  deactivated_at: string | null;
  blocked: boolean;
};

type CompanyCardData = CompanySummary & {
  legal_name: string;
  setup_completed_at: string | null;
  contacts_masked: { email: string; phone: string };
  employees: number;
  owners: number;
  blocks: Block[];
};

/** § 24.1.2: search of users and companies with minimal information. */
export function DirectoryPage() {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const kind = params.get("kind") === "companies" ? "companies" : "users";
  const query = params.get("q") ?? "";
  const [text, setText] = useState(query);
  const path = `/staff/${kind}?q=${encodeURIComponent(query)}`;
  const found = useApi<{ users?: UserSummary[]; companies?: CompanySummary[] }>(["staff", kind, query], path, query !== "");
  return (
    <div className="page">
      <h1>{t("staff.nav_directory")}</h1>
      <div className="tabs">
        {(["users", "companies"] as const).map((k) => (
          <button key={k} type="button" className={k === kind ? "active" : ""} onClick={() => setParams({ kind: k, q: query })}>
            {t(`staff.tab_${k}`)}
          </button>
        ))}
      </div>
      <form
        className="row"
        onSubmit={(e) => {
          e.preventDefault();
          setParams({ kind, q: text.trim() });
        }}
      >
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder={t(`staff.search_${kind}`)} autoFocus />
        <div className="actions" style={{ marginTop: 0, flex: "0 0 auto" }}>
          <button type="submit" disabled={!text.trim()}>
            {t("staff.search")}
          </button>
        </div>
      </form>
      {found.isFetching ? <Loading /> : null}
      <ErrorMessage error={found.error as ApiError | null} />
      {found.data?.users?.length === 0 || found.data?.companies?.length === 0 ? <p className="muted">{t("staff.nothing_found")}</p> : null}
      <div className="stack">
        {found.data?.users?.map((u) => (
          <Link key={u.public_id} className="list-link" to={`/users/${u.public_id}`}>
            <div className="list-link-head">
              <strong>{u.name || u.public_id}</strong>
              <span>
                {u.blocked ? <span className="badge danger">{t("staff.blocked")}</span> : null}{" "}
                {u.is_staff ? <span className="badge">{t("staff.is_staff")}</span> : null}
              </span>
            </div>
            <div className="muted small">
              {u.public_id} · {u.email_masked} {u.username ? `· @${u.username}` : ""}
            </div>
          </Link>
        ))}
        {found.data?.companies?.map((c) => (
          <Link key={c.public_id} className="list-link" to={`/companies/${c.public_id}`}>
            <div className="list-link-head">
              <strong>{c.display_name}</strong>
              <span>
                {c.blocked ? <span className="badge danger">{t("staff.blocked")}</span> : null}{" "}
                {c.deactivated_at ? <span className="badge warn">{t("staff.deactivated")}</span> : null}
              </span>
            </div>
            <div className="muted small">
              {c.public_id} {c.country ? `· ${c.country}` : ""}
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}

/** The masked contacts are shown by a separate action with a reason, journaled (§ 24.4.3). */
function Reveal({ path }: { path: string }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [shown, setShown] = useState<Record<string, string | string[]> | null>(null);
  const command = useCommand<{ reason: string }, { contacts: Record<string, string | string[]> }>(path);
  if (shown) {
    return (
      <dl className="facts">
        {Object.entries(shown).map(([k, v]) => (
          <div key={k}>
            <dt>{t(`staff.contact_${k}`)}</dt>
            <dd>{Array.isArray(v) ? v.join(", ") || "—" : v || "—"}</dd>
          </div>
        ))}
      </dl>
    );
  }
  if (!open) {
    return (
      <button type="button" className="secondary" onClick={() => setOpen(true)}>
        {t("staff.reveal")}
      </button>
    );
  }
  return (
    <ReasonForm
      label={t("staff.reveal_reason")}
      submit={t("staff.reveal")}
      pending={command.pending}
      error={command.error}
      onCancel={() => setOpen(false)}
      onSubmit={async (reason) => {
        const result = await command.run({ reason });
        if (result) setShown(result.contacts);
      }}
    />
  );
}

export function BlockHistory({ blocks, onChanged }: { blocks: Block[]; onChanged: () => void }) {
  const { t } = useTranslation();
  if (blocks.length === 0) return <p className="muted">{t("staff.no_blocks")}</p>;
  return (
    <div className="stack">
      {blocks.map((b) => (
        <BlockRow key={b.id} block={b} onChanged={onChanged} />
      ))}
    </div>
  );
}

function BlockRow({ block: b, onChanged }: { block: Block; onChanged: () => void }) {
  const { t } = useTranslation();
  const when = useWhen();
  const can = useStaffCan();
  const [restoring, setRestoring] = useState(false);
  const restore = useCommand<{ reason: string }, { block: Block }>(`/staff/blocks/${b.id}/restore`);
  const release = useCommand<Record<string, never>, { block: Block }>(`/staff/blocks/${b.id}/notice/release`);
  return (
    <div className="list-link">
      <div className="list-link-head">
        <strong>{t(`staff.block_category_${b.category}`)}</strong>
        {b.ended_at ? (
          <span className="badge">{t(`staff.block_end_${b.end_kind ?? "manual"}`)}</span>
        ) : (
          <span className="badge danger">{t("staff.block_active")}</span>
        )}
      </div>
      <div className="small">{b.comment}</div>
      {b.disclosed_reason ? (
        <div className="muted small">
          {t("staff.block_disclosed")}: {b.disclosed_reason}
        </div>
      ) : null}
      <div className="muted small">
        {when(b.started_at)} —{" "}
        {b.ended_at ? when(b.ended_at) : b.ends_at ? `${t("staff.block_until")} ${when(b.ends_at)}` : t("staff.block_indefinite")}
        {" · "}
        {t(`staff.notice_${b.notice}`)}
      </div>
      {b.end_reason ? (
        <div className="muted small">
          {t("staff.restore_reason")}: {b.end_reason}
        </div>
      ) : null}
      {b.notice === "deferred" && can("block.notice_defer") ? (
        <div className="actions">
          <button
            type="button"
            className="secondary"
            disabled={release.pending}
            onClick={async () => {
              if (await release.run({})) onChanged();
            }}
          >
            {t("staff.notice_release")}
          </button>
        </div>
      ) : null}
      <ErrorMessage error={release.error} />
      {!b.ended_at && can("block.manage") ? (
        restoring ? (
          <ReasonForm
            label={t("staff.restore_reason")}
            submit={t("staff.restore")}
            pending={restore.pending}
            error={restore.error}
            onCancel={() => setRestoring(false)}
            onSubmit={async (reason) => {
              if (await restore.run({ reason })) {
                setRestoring(false);
                onChanged();
              }
            }}
          />
        ) : (
          <div className="actions">
            <button type="button" onClick={() => setRestoring(true)}>
              {t("staff.restore")}
            </button>
          </div>
        )
      ) : null}
    </div>
  );
}

function Actions({ targetType, target, blocked, onChanged }: { targetType: "company" | "user"; target: string; blocked: boolean; onChanged: () => void }) {
  const { t } = useTranslation();
  const can = useStaffCan();
  const [blocking, setBlocking] = useState(false);
  if (blocking) {
    return (
      <Card title={t("staff.block_title")}>
        <BlockForm
          targetType={targetType}
          target={target}
          onDone={() => {
            setBlocking(false);
            onChanged();
          }}
        />
      </Card>
    );
  }
  return (
    <div className="actions">
      {can("privileged.open") ? (
        <Link className="button" to={`/sessions?type=${targetType}&target=${target}`}>
          {t("staff.open_session")}
        </Link>
      ) : null}
      {can("block.manage") && !blocked ? (
        <button type="button" className="danger" onClick={() => setBlocking(true)}>
          {t("staff.block")}
        </button>
      ) : null}
    </div>
  );
}

export function UserCard() {
  const { t } = useTranslation();
  const { usr = "" } = useParams();
  const when = useWhen();
  const can = useStaffCan();
  const client = useQueryClient();
  const card = useApi<{ user: UserCardData }>(["staff", "user", usr], `/staff/users/${usr}`);
  const refresh = () => void client.invalidateQueries({ queryKey: ["staff", "user", usr] });
  if (card.isLoading) return <Loading />;
  if (!card.data) return <ErrorMessage error={card.error as ApiError | null} />;
  const u = card.data.user;
  return (
    <div className="page">
      <h1>{u.name || u.public_id}</h1>
      <p className="muted">
        {u.public_id} · {t("staff.registered")} {when(u.created_at)} · {t(`staff.user_state_${u.state}`, { defaultValue: u.state })}
        {u.blocked ? <span className="badge danger"> {t("staff.blocked")}</span> : null}
      </p>
      <Card title={t("staff.contacts")}>
        <dl className="facts">
          <div>
            <dt>{t("staff.contact_login_emails")}</dt>
            <dd>{u.contacts_masked.login_emails.join(", ") || "—"}</dd>
          </div>
          <div>
            <dt>{t("staff.contact_contact_email")}</dt>
            <dd>{u.contacts_masked.contact_email || "—"}</dd>
          </div>
          <div>
            <dt>{t("staff.contact_contact_phone")}</dt>
            <dd>{u.contacts_masked.contact_phone || "—"}</dd>
          </div>
        </dl>
        {can("contacts.reveal") ? <Reveal path={`/staff/users/${u.public_id}/contacts`} /> : null}
      </Card>
      <Card title={t("staff.user_companies")}>
        {u.companies.length === 0 ? <p className="muted">—</p> : null}
        {u.companies.map((c) => (
          <Link key={c.public_id} className="list-link" to={`/companies/${c.public_id}`}>
            <strong>{c.display_name}</strong>
            <div className="muted small">
              {c.public_id} {c.top_authority ? `· ${t("staff.top_authority")}` : ""}
            </div>
          </Link>
        ))}
        <p className="muted small">{t("staff.open_sessions", { count: u.open_sessions })}</p>
      </Card>
      <Actions targetType="user" target={u.public_id} blocked={u.blocked} onChanged={refresh} />
      <Card title={t("staff.blocks")}>
        <BlockHistory blocks={u.blocks} onChanged={refresh} />
      </Card>
    </div>
  );
}

export function CompanyCard() {
  const { t } = useTranslation();
  const { cmp = "" } = useParams();
  const when = useWhen();
  const can = useStaffCan();
  const client = useQueryClient();
  const card = useApi<{ company: CompanyCardData }>(["staff", "company", cmp], `/staff/companies/${cmp}`);
  const refresh = () => void client.invalidateQueries({ queryKey: ["staff", "company", cmp] });
  if (card.isLoading) return <Loading />;
  if (!card.data) return <ErrorMessage error={card.error as ApiError | null} />;
  const c = card.data.company;
  return (
    <div className="page">
      <h1>{c.display_name}</h1>
      <p className="muted">
        {c.public_id} · {t("staff.registered")} {when(c.created_at)}
        {c.blocked ? <span className="badge danger"> {t("staff.blocked")}</span> : null}
        {c.deactivated_at ? <span className="badge warn"> {t("staff.deactivated")}</span> : null}
      </p>
      <Card title={t("staff.company_info")}>
        <dl className="facts">
          <div>
            <dt>{t("staff.legal_name")}</dt>
            <dd>{c.legal_name || "—"}</dd>
          </div>
          <div>
            <dt>{t("staff.country")}</dt>
            <dd>{c.country || "—"}</dd>
          </div>
          <div>
            <dt>{t("staff.employees_count")}</dt>
            <dd>{c.employees}</dd>
          </div>
          <div>
            <dt>{t("staff.owners_count")}</dt>
            <dd>{c.owners}</dd>
          </div>
          <div>
            <dt>{t("staff.contact_email")}</dt>
            <dd>{c.contacts_masked.email || "—"}</dd>
          </div>
          <div>
            <dt>{t("staff.contact_phone")}</dt>
            <dd>{c.contacts_masked.phone || "—"}</dd>
          </div>
        </dl>
        {can("contacts.reveal") ? <Reveal path={`/staff/companies/${c.public_id}/contacts`} /> : null}
      </Card>
      <Actions targetType="company" target={c.public_id} blocked={c.blocked} onChanged={refresh} />
      <Card title={t("staff.blocks")}>
        <BlockHistory blocks={c.blocks} onChanged={refresh} />
      </Card>
    </div>
  );
}
