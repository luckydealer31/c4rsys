import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { ApiError } from "../api/client";
import { useApi } from "../api/queries";
import { useCommand } from "../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../components/ui";
import { BlockHistory } from "./Directory";
import { ReasonForm, useStaff, useStaffCan, useWhen, type Block } from "./common";

type Role = { code: string; name: string; permissions: string[]; is_starter: boolean };
type Member = {
  id: string;
  user: { public_id: string; name: string };
  unit: string;
  state: string;
  roles: string[];
  created_at: string;
  revoked_at: string | null;
  version: number;
};

function useRoleName() {
  const { t } = useTranslation();
  return (role: Role | string) => {
    const code = typeof role === "string" ? role : role.code;
    const name = typeof role === "string" ? "" : role.name;
    return t(`staff.role_${code}`, { defaultValue: name || code });
  };
}

/** § 24.1.5–24.1.6: accounts of the system staff and their system sets. */
export function MembersPage() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const roleName = useRoleName();
  const members = useApi<{ members: Member[] }>(["staff", "members"], "/staff/members");
  const roles = useApi<{ roles: Role[]; permissions: string[] }>(["staff", "roles"], "/staff/roles");
  const [user, setUser] = useState("");
  const [unit, setUnit] = useState("");
  const [chosen, setChosen] = useState<string[]>([]);
  const add = useCommand<{ user: string; unit: string; roles: string[] }, { member: Member }>("/staff/members");
  const refresh = () => void client.invalidateQueries({ queryKey: ["staff", "members"] });
  const allRoles = roles.data?.roles ?? [];
  return (
    <div className="page">
      <h1>{t("staff.nav_members")}</h1>
      {members.isLoading ? <Loading /> : null}
      <ErrorMessage error={members.error as ApiError | null} />
      <div className="stack">
        {members.data?.members.map((m) => (
          <MemberRow key={m.id} member={m} roles={allRoles} onChanged={refresh} />
        ))}
      </div>
      <Card title={t("staff.member_add")}>
        <form
          className="stack"
          onSubmit={async (e) => {
            e.preventDefault();
            if (await add.run({ user: user.trim(), unit, roles: chosen })) {
              setUser("");
              setUnit("");
              setChosen([]);
              refresh();
            }
          }}
        >
          <div className="row">
            <Field label={t("staff.member_user")}>
              <input value={user} placeholder="USR-…" onChange={(e) => setUser(e.target.value)} />
            </Field>
            <Field label={t("staff.member_unit")} hint={t("staff.member_unit_hint")}>
              <input value={unit} onChange={(e) => setUnit(e.target.value)} />
            </Field>
          </div>
          <RolePicker roles={allRoles} chosen={chosen} onChange={setChosen} />
          <ErrorMessage error={add.error} />
          <div className="actions">
            <button type="submit" disabled={add.pending || !user.trim()}>
              {t("staff.member_add")}
            </button>
          </div>
        </form>
      </Card>
      <RolesCard roles={allRoles} permissions={roles.data?.permissions ?? []} roleName={roleName} />
    </div>
  );
}

function RolePicker({ roles, chosen, onChange }: { roles: Role[]; chosen: string[]; onChange: (codes: string[]) => void }) {
  const roleName = useRoleName();
  return (
    <div className="chips">
      {roles.map((r) => (
        <label key={r.code} className="check">
          <input
            type="checkbox"
            checked={chosen.includes(r.code)}
            onChange={(e) => onChange(e.target.checked ? [...chosen, r.code] : chosen.filter((x) => x !== r.code))}
          />{" "}
          {roleName(r)}
        </label>
      ))}
    </div>
  );
}

function MemberRow({ member: m, roles, onChanged }: { member: Member; roles: Role[]; onChanged: () => void }) {
  const { t } = useTranslation();
  const roleName = useRoleName();
  const me = useStaff();
  const [editing, setEditing] = useState(false);
  const [revoking, setRevoking] = useState(false);
  const [chosen, setChosen] = useState(m.roles);
  const [unit, setUnit] = useState(m.unit);
  const change = useCommand<{ roles: string[]; unit: string; version: number }, { member: Member }>(`/staff/members/${m.id}`);
  const revoke = useCommand<{ reason: string; version: number }, { member: Member }>(`/staff/members/${m.id}/revoke`);
  return (
    <div className="list-link">
      <div className="list-link-head">
        <strong>
          {m.user.name || m.user.public_id} <span className="muted small">{m.user.public_id}</span>
        </strong>
        <span className={m.state === "active" ? "badge ok" : "badge"}>{t(`staff.member_state_${m.state}`)}</span>
      </div>
      <div className="muted small">
        {m.unit || "—"} · {m.roles.map(roleName).join(", ") || t("staff.no_roles")}
      </div>
      {m.state === "active" && !editing && !revoking ? (
        <div className="actions">
          <button type="button" className="secondary" onClick={() => setEditing(true)}>
            {t("common.edit")}
          </button>
          {m.id !== me.member.id ? (
            <button type="button" className="secondary" onClick={() => setRevoking(true)}>
              {t("staff.member_revoke")}
            </button>
          ) : null}
        </div>
      ) : null}
      {editing ? (
        <form
          className="stack"
          onSubmit={async (e) => {
            e.preventDefault();
            if (await change.run({ roles: chosen, unit, version: m.version })) {
              setEditing(false);
              onChanged();
            }
          }}
        >
          <Field label={t("staff.member_unit")}>
            <input value={unit} onChange={(e) => setUnit(e.target.value)} />
          </Field>
          <RolePicker roles={roles} chosen={chosen} onChange={setChosen} />
          <ErrorMessage error={change.error} />
          <div className="actions">
            <button type="submit" disabled={change.pending}>
              {t("common.save")}
            </button>
            <button type="button" className="secondary" onClick={() => setEditing(false)}>
              {t("common.cancel")}
            </button>
          </div>
        </form>
      ) : null}
      {revoking ? (
        <ReasonForm
          label={t("staff.member_revoke_reason")}
          submit={t("staff.member_revoke")}
          pending={revoke.pending}
          error={revoke.error}
          onCancel={() => setRevoking(false)}
          onSubmit={async (reason) => {
            if (await revoke.run({ reason, version: m.version })) onChanged();
          }}
        />
      ) : null}
    </div>
  );
}

function RolesCard({ roles, permissions, roleName }: { roles: Role[]; permissions: string[]; roleName: (r: Role) => string }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [chosen, setChosen] = useState<string[]>([]);
  const create = useCommand<{ code: string; name: string; permissions: string[] }, { role: Role }>("/staff/roles");
  const permissionName = (p: string) => t(`staff.permission_${p.replaceAll(".", "_")}`);
  return (
    <Card title={t("staff.roles")}>
      <div className="stack">
        {roles.map((r) => (
          <div key={r.code} className="small">
            <strong>{roleName(r)}</strong>
            {r.is_starter ? <span className="muted"> · {t("staff.role_starter")}</span> : null}
            <div className="muted">{r.permissions.map(permissionName).join(", ")}</div>
          </div>
        ))}
      </div>
      <h3>{t("staff.role_new")}</h3>
      <form
        className="stack"
        onSubmit={async (e) => {
          e.preventDefault();
          if (await create.run({ code, name, permissions: chosen })) {
            setCode("");
            setName("");
            setChosen([]);
            void client.invalidateQueries({ queryKey: ["staff", "roles"] });
          }
        }}
      >
        <div className="row">
          <Field label={t("staff.role_code")} hint={t("staff.role_code_hint")}>
            <input value={code} onChange={(e) => setCode(e.target.value)} />
          </Field>
          <Field label={t("staff.role_name")}>
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
        </div>
        <div className="chips">
          {permissions.map((p) => (
            <label key={p} className="check">
              <input type="checkbox" checked={chosen.includes(p)} onChange={(e) => setChosen(e.target.checked ? [...chosen, p] : chosen.filter((x) => x !== p))} />{" "}
              {permissionName(p)}
            </label>
          ))}
        </div>
        <ErrorMessage error={create.error} />
        <div className="actions">
          <button type="submit" disabled={create.pending || !code.trim() || chosen.length === 0}>
            {t("staff.role_create")}
          </button>
        </div>
      </form>
    </Card>
  );
}

/** § 24.2: the blocks of users and companies. */
export function BlocksPage() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [active, setActive] = useState(true);
  const list = useApi<{ blocks: Block[] }>(["staff", "blocks", active], `/staff/blocks${active ? "?active=1" : ""}`);
  return (
    <div className="page">
      <h1>{t("staff.nav_blocks")}</h1>
      <p className="muted">{t("staff.blocks_hint")}</p>
      <label className="check">
        <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} /> {t("staff.blocks_active_only")}
      </label>
      {list.isLoading ? <Loading /> : null}
      <ErrorMessage error={list.error as ApiError | null} />
      {list.data?.blocks.map((b) => (
        <Card key={b.id} title={b.company?.display_name ?? b.user?.name ?? b.user?.public_id ?? ""}>
          <BlockHistory blocks={[b]} onChanged={() => void client.invalidateQueries({ queryKey: ["staff", "blocks"] })} />
        </Card>
      ))}
    </div>
  );
}

type Entry = {
  id: string;
  occurred_at: string;
  actor: string | null;
  system_process: string | null;
  roles: string[];
  action: string;
  object_type: string;
  object_id: string;
  reason: string;
  result: string;
  before: unknown;
  after: unknown;
};

/** § 24.4.5: the system journal; it is only read. Export is a separate permission. */
export function JournalPage() {
  const { t } = useTranslation();
  const when = useWhen();
  const can = useStaffCan();
  const [action, setAction] = useState("");
  const [applied, setApplied] = useState("");
  const query = applied ? `?action=${encodeURIComponent(applied)}` : "";
  const list = useApi<{ entries: Entry[] }>(["staff", "journal", applied], `/staff/journal${query}`);
  return (
    <div className="page wide">
      <h1>{t("staff.nav_journal")}</h1>
      <form
        className="row"
        onSubmit={(e) => {
          e.preventDefault();
          setApplied(action.trim());
        }}
      >
        <input value={action} placeholder={t("staff.journal_action")} onChange={(e) => setAction(e.target.value)} />
        <div className="actions" style={{ marginTop: 0, flex: "0 0 auto" }}>
          <button type="submit">{t("staff.search")}</button>
          {can("journal.export") ? (
            <a className="button secondary" href={`/api/staff/journal/export${query}`}>
              {t("staff.journal_export")}
            </a>
          ) : null}
        </div>
      </form>
      {list.isLoading ? <Loading /> : null}
      <ErrorMessage error={list.error as ApiError | null} />
      <div className="table-scroll">
        <table className="table">
          <thead>
            <tr>
              <th>{t("staff.journal_when")}</th>
              <th>{t("staff.journal_who")}</th>
              <th>{t("staff.journal_action")}</th>
              <th>{t("staff.journal_object")}</th>
              <th>{t("staff.journal_reason")}</th>
            </tr>
          </thead>
          <tbody>
            {list.data?.entries.map((e) => (
              <tr key={e.id}>
                <td className="small">{when(e.occurred_at)}</td>
                <td className="small">
                  {e.actor ?? e.system_process}
                  {e.roles.length ? <div className="muted">{e.roles.join(", ")}</div> : null}
                </td>
                <td className="small">
                  <code>{e.action}</code>
                </td>
                <td className="small">
                  {e.object_type} {e.object_id}
                </td>
                <td className="small">{e.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
