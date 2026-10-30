import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { useApi } from "../../api/queries";
import type { OrgNode, OrgStructure, PositionEntry } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";

type Org = OrgStructure;
type Department = Org["departments"][number];

export function useOrg() {
  const company = useCompany();
  return useApi<{ org: Org }>(["org", company.public_id], `/c/${company.public_id}/org`);
}

export function usePositions() {
  const company = useCompany();
  return useApi<{ positions: PositionEntry[] }>(
    ["positions", company.public_id],
    `/c/${company.public_id}/positions`,
  );
}

export function positionLabel(p: { code: string; name: string }, t: (key: string) => string) {
  return p.name || (p.code ? t(`positions.${p.code}`) : "—");
}

/** The name of an employee or a department of the structure. */
export function nodeName(org: Org | undefined, node: OrgNode, t: (key: string) => string): string {
  if (!org) return "…";
  if (node.type === "department") return org.departments.find((d) => d.id === node.id)?.name ?? "—";
  const e = org.employees.find((x) => x.id === node.id);
  return e ? e.name || t("employees.not_linked") : "—";
}

const key = (node: OrgNode) => `${node.type}:${node.id}`;

/** Children of a node: subordinate employees and departments (§ 5.2.1). */
function childrenOf(org: Org, node: OrgNode): OrgNode[] {
  const employees = org.links
    .filter((l) => l.responsible.type === node.type && l.responsible.id === node.id)
    .map((l) => ({ type: "employee", id: l.subordinate }) as OrgNode);
  const departments = org.departments
    .filter((d) => d.parent.type === node.type && d.parent.id === node.id)
    .map((d) => ({ type: "department", id: d.id }) as OrgNode);
  return [...departments, ...employees];
}

/** Organisation structure: departments, responsibles and subordination as a tree (§ 5.2). */
export function OrgPage() {
  const { t } = useTranslation();
  const can = useCan();
  const org = useOrg();
  const data = org.data?.org;
  const manage = can("org.manage");
  if (org.isLoading) return <div className="page"><Loading /></div>;
  if (!data) return null;
  const roots: OrgNode[] = data.top ? [{ type: "employee", id: data.top }] : [];
  return (
    <div className="page">
      <h1>{t("org.title")}</h1>
      <p className="muted small">{t("org.hint")}</p>
      <Card>
        <ul className="org-tree">
          {roots.map((root) => (
            <TreeNode key={key(root)} org={data} node={root} path={[]} />
          ))}
        </ul>
        <Outside org={data} roots={roots} />
      </Card>
      {manage ? <NewDepartment org={data} /> : null}
      {data.departments.map((d) => (
        <DepartmentCard key={d.id} org={data} department={d} manage={manage} />
      ))}
    </div>
  );
}

function TreeNode({ org, node, path }: { org: Org; node: OrgNode; path: string[] }) {
  const { t } = useTranslation();
  const company = useCompany();
  const id = key(node);
  // The server refuses cycles; the path guard only keeps the view safe.
  const children = path.includes(id) ? [] : childrenOf(org, node);
  let label: ReactNode;
  if (node.type === "department") {
    const department = org.departments.find((d) => d.id === node.id);
    label = (
      <>
        <span className="badge">{t("org.department")}</span> <strong>{department?.name}</strong>
        {department?.members.length ? (
          <div className="muted small">
            {t("org.members")}: {department.members.map((m) => nodeName(org, { type: "employee", id: m }, t)).join(", ")}
          </div>
        ) : null}
      </>
    );
  } else {
    const employee = org.employees.find((e) => e.id === node.id);
    label = (
      <>
        <Link to={companyPath(company, `/clients/employees/${node.id}`)}>
          {employee ? employee.name || t("employees.not_linked") : "—"}
        </Link>
        {employee ? (
          <span className="muted small">
            {" · "}
            {positionLabel(employee.position, t)}
            {employee.state !== "active" ? ` · ${t(`employees.state_${employee.state}`)}` : ""}
          </span>
        ) : null}
        {employee?.is_top_authority ? <span className="badge ok">{t("org.top")}</span> : null}
      </>
    );
  }
  return (
    <li>
      <div className="org-node">{label}</div>
      {children.length ? (
        <ul>
          {children.map((child) => (
            <TreeNode key={key(child)} org={org} node={child} path={[...path, id]} />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

/** Employees and departments not reachable from the top (e.g. no top manager). */
function Outside({ org, roots }: { org: Org; roots: OrgNode[] }) {
  const { t } = useTranslation();
  const seen = new Set<string>();
  const walk = (node: OrgNode) => {
    if (seen.has(key(node))) return;
    seen.add(key(node));
    childrenOf(org, node).forEach(walk);
  };
  roots.forEach(walk);
  const rest = org.employees.filter((e) => !seen.has(`employee:${e.id}`));
  if (!rest.length) return null;
  return (
    <p className="muted small">
      {t("org.outside")}: {rest.map((e) => e.name || t("employees.not_linked")).join(", ")}
    </p>
  );
}

/** Choice of a parent or a responsible: an active employee or a department. */
export function NodeSelect({
  org,
  value,
  onChange,
  exclude,
  departments = true,
}: {
  org: Org;
  value: string;
  onChange: (value: string) => void;
  exclude?: string;
  departments?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">—</option>
      <optgroup label={t("org.employees")}>
        {org.employees
          .filter((e) => e.state === "active" && `employee:${e.id}` !== exclude)
          .map((e) => (
            <option key={e.id} value={`employee:${e.id}`}>
              {e.name || t("employees.not_linked")}
            </option>
          ))}
      </optgroup>
      {departments ? (
        <optgroup label={t("org.departments")}>
          {org.departments
            .filter((d) => `department:${d.id}` !== exclude)
            .map((d) => (
              <option key={d.id} value={`department:${d.id}`}>
                {d.name}
              </option>
            ))}
        </optgroup>
      ) : null}
    </select>
  );
}

export function parseNode(value: string): OrgNode | null {
  const [type, id] = value.split(":");
  return type && id ? ({ type, id } as OrgNode) : null;
}

function useRefreshOrg() {
  const company = useCompany();
  const client = useQueryClient();
  return async () => {
    await client.invalidateQueries({ queryKey: ["org", company.public_id] });
    await client.invalidateQueries({ queryKey: ["employee", company.public_id] });
  };
}

function NewDepartment({ org }: { org: Org }) {
  const { t } = useTranslation();
  const company = useCompany();
  const refresh = useRefreshOrg();
  const [name, setName] = useState("");
  const [parent, setParent] = useState("");
  const create = useCommand<Record<string, unknown>, unknown>(`/c/${company.public_id}/org/departments`);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const body: Record<string, unknown> = { name };
    const node = parseNode(parent);
    if (node) body.parent = node;
    if ((await create.run(body)) !== null) {
      setName("");
      setParent("");
      await refresh();
    }
  }

  return (
    <Card title={t("org.new_department")}>
      <form className="stack" onSubmit={submit}>
        <div className="row">
          <Field label={t("org.department_name")}>
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label={t("org.parent")} hint={t("org.parent_hint")}>
            <NodeSelect org={org} value={parent} onChange={setParent} />
          </Field>
        </div>
        <ErrorMessage error={create.error} />
        <div className="actions">
          <button type="submit" disabled={!name.trim() || create.pending}>
            {t("org.create")}
          </button>
        </div>
      </form>
    </Card>
  );
}

function DepartmentCard({ org, department, manage }: { org: Org; department: Department; manage: boolean }) {
  const { t } = useTranslation();
  const company = useCompany();
  const refresh = useRefreshOrg();
  const base = `/c/${company.public_id}/org/departments/${department.id}`;
  const [name, setName] = useState(department.name);
  const [parent, setParent] = useState(key(department.parent));
  const [member, setMember] = useState("");
  const change = useCommand<Record<string, unknown>, unknown>(base, "PATCH");
  const archive = useCommand<Record<string, unknown>, unknown>(`${base}/archive`);
  const add = useCommand<Record<string, unknown>, unknown>(`${base}/members`);
  const remove = useCommand<{ id: string }, unknown>((body) => `${base}/members/${body.id}/remove`);
  const error = change.error ?? archive.error ?? add.error ?? remove.error;

  async function save(event: FormEvent) {
    event.preventDefault();
    const body: Record<string, unknown> = { expected_version: department.version };
    if (name !== department.name) body.name = name;
    const node = parseNode(parent);
    if (node && key(node) !== key(department.parent)) body.parent = node;
    if (Object.keys(body).length > 1 && (await change.run(body)) !== null) await refresh();
  }

  return (
    <Card title={department.name}>
      <p className="small">
        {t("org.parent")}: {nodeName(org, department.parent, t)}
      </p>
      <p className="small">
        {t("org.members")}:{" "}
        {department.members.length === 0 ? "—" : null}
      </p>
      <ul className="plain">
        {department.members.map((m) => (
          <li key={m} className="small">
            {nodeName(org, { type: "employee", id: m }, t)}{" "}
            {manage ? (
              <button
                type="button"
                className="secondary small"
                onClick={async () => {
                  if ((await remove.run({ id: m })) !== null) await refresh();
                }}
              >
                {t("org.remove_member")}
              </button>
            ) : null}
          </li>
        ))}
      </ul>
      {manage ? (
        <>
          <div className="row">
            <Field label={t("org.add_member")}>
              <select value={member} onChange={(e) => setMember(e.target.value)}>
                <option value="">—</option>
                {org.employees
                  .filter((e) => !department.members.includes(e.id))
                  .map((e) => (
                    <option key={e.id} value={e.id}>
                      {e.name || t("employees.not_linked")}
                    </option>
                  ))}
              </select>
            </Field>
            <div className="actions">
              <button
                type="button"
                disabled={!member || add.pending}
                onClick={async () => {
                  if ((await add.run({ employment_id: member })) !== null) {
                    setMember("");
                    await refresh();
                  }
                }}
              >
                {t("org.add")}
              </button>
            </div>
          </div>
          <form className="stack" onSubmit={save}>
            <div className="row">
              <Field label={t("org.department_name")}>
                <input value={name} onChange={(e) => setName(e.target.value)} />
              </Field>
              <Field label={t("org.parent")}>
                <NodeSelect
                  org={org}
                  value={parent}
                  onChange={setParent}
                  exclude={`department:${department.id}`}
                />
              </Field>
            </div>
            <div className="actions">
              <button type="submit" disabled={change.pending}>
                {t("common.save")}
              </button>
              <button
                type="button"
                className="danger"
                disabled={archive.pending}
                onClick={async () => {
                  if ((await archive.run({ expected_version: department.version })) !== null) await refresh();
                }}
              >
                {t("org.archive")}
              </button>
            </div>
          </form>
        </>
      ) : null}
      <ErrorMessage error={error} />
    </Card>
  );
}

/** The company's handbook of positions (§ 5.1.5): names for reuse, they give no rights. */
export function PositionsPage() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const positions = usePositions();
  const [name, setName] = useState("");
  const create = useCommand<Record<string, unknown>, unknown>(`/c/${company.public_id}/positions`);
  const manage = can("employee.positions.manage");
  const refresh = () => client.invalidateQueries({ queryKey: ["positions", company.public_id] });

  return (
    <div className="page">
      <h1>{t("positions_page.title")}</h1>
      <p className="muted small">{t("positions_page.hint")}</p>
      <Card>
        {positions.isLoading ? <Loading /> : null}
        <table>
          <thead>
            <tr>
              <th>{t("positions_page.name")}</th>
              <th>{t("positions_page.employees")}</th>
            </tr>
          </thead>
          <tbody>
            {(positions.data?.positions ?? []).map((p) => (
              <PositionRow key={p.id} position={p} manage={manage && !p.is_dedicated} onSaved={refresh} />
            ))}
          </tbody>
        </table>
      </Card>
      {manage ? (
        <Card title={t("positions_page.add")}>
          <form
            className="row"
            onSubmit={async (event) => {
              event.preventDefault();
              if ((await create.run({ name })) !== null) {
                setName("");
                await refresh();
              }
            }}
          >
            <Field label={t("positions_page.name")}>
              <input value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
            <div className="actions">
              <button type="submit" disabled={!name.trim() || create.pending}>
                {t("positions_page.add")}
              </button>
            </div>
          </form>
          <ErrorMessage error={create.error} />
        </Card>
      ) : null}
    </div>
  );
}

function PositionRow({
  position,
  manage,
  onSaved,
}: {
  position: PositionEntry;
  manage: boolean;
  onSaved: () => Promise<void>;
}) {
  const { t } = useTranslation();
  const company = useCompany();
  const [name, setName] = useState(position.name);
  const rename = useCommand<Record<string, unknown>, unknown>(
    `/c/${company.public_id}/positions/${position.id}`,
    "PATCH",
  );
  return (
    <tr>
      <td>
        {manage ? (
          <form
            className="row"
            onSubmit={async (event) => {
              event.preventDefault();
              if ((await rename.run({ name })) !== null) await onSaved();
            }}
          >
            <input aria-label={t("positions_page.name")} value={name} onChange={(e) => setName(e.target.value)} />
            <button type="submit" className="secondary" disabled={!name.trim() || name === position.name}>
              {t("common.save")}
            </button>
          </form>
        ) : (
          <>
            {positionLabel(position, t)}
            {position.is_dedicated ? <div className="muted small">{t("positions_page.dedicated")}</div> : null}
          </>
        )}
        <ErrorMessage error={rename.error} />
      </td>
      <td>{position.employees}</td>
    </tr>
  );
}
