import { useQueryClient } from "@tanstack/react-query";
import { DiscountLimitCard } from "../orders/Discounts";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router";

import { useApi } from "../../api/queries";
import type { Employee, JournalEntry, OrgNode, PermissionCatalog, RightSpec } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { formatMoment } from "../../time";
import { useLocations } from "./LocationsPage";
import { Avatar } from "../../components/images";
import { NodeSelect, nodeName, parseNode, positionLabel, useOrg, usePositions } from "./OrgPage";
import { cleanRights, RightsEditor } from "./RightsEditor";

export function useEmployees() {
  const company = useCompany();
  return useApi<{ employees: Employee[] }>(
    ["employees", company.public_id],
    `/c/${company.public_id}/employees`,
  );
}

export function useCatalog() {
  const company = useCompany();
  return useApi<PermissionCatalog>(
    ["permission-catalog", company.public_id],
    `/c/${company.public_id}/permission-catalog`,
  );
}

export function employeeName(employee: Employee, t: (key: string) => string): string {
  if (employee.user) return `${employee.user.first_name} ${employee.user.last_name}`.trim();
  return t("employees.not_linked");
}

export function positionName(position: Employee["position"], t: (key: string) => string) {
  return position.name || (position.code ? t(`positions.${position.code}`) : "—");
}

/** Employees: list, invitation by ID or email with position and rights (§ 5.1, § 23.1). */
/** Employees of the company; shown as a tab of «Clients and counterparties» (decision of
 * 24.10.2026). */
export function EmployeesList() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const employees = useEmployees();
  return (
    <>
      <p className="small">
        <Link to={companyPath(company, "/settings/org")}>{t("org.title")}</Link>
        {" · "}
        <Link to={companyPath(company, "/settings/positions")}>{t("positions_page.title")}</Link>
      </p>
      <Card>
        {employees.isLoading ? <Loading /> : null}
        <table>
          <thead>
            <tr>
              <th>{t("employees.name")}</th>
              <th>{t("employees.position")}</th>
              <th>{t("employees.state")}</th>
              <th>{t("employees.rights")}</th>
            </tr>
          </thead>
          <tbody>
            {(employees.data?.employees ?? []).map((employee) => (
              <tr key={employee.id}>
                <td>
                  <Link to={companyPath(company, `/clients/employees/${employee.id}`)}>
                    {employeeName(employee, t)}
                  </Link>
                  <div className="muted small">{employee.user?.public_id ?? ""}</div>
                </td>
                <td>{positionName(employee.position, t)}</td>
                <td>{t(`employees.state_${employee.state}`)}</td>
                <td className="small">
                  {employee.position.is_top_authority
                    ? t("employees.top_authority")
                    : employee.rights.map((r) => (r.set ? t(`sets.${r.set}`) : r.permission)).join(", ") ||
                      "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      {can("employee.invite") ? <InviteForm /> : null}
    </>
  );
}

function InviteForm() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const catalog = useCatalog();
  const locations = useLocations();
  const [target, setTarget] = useState("");
  const [position, setPosition] = useState("");
  const [rights, setRights] = useState<RightSpec[]>([]);
  const [sent, setSent] = useState(false);
  const invite = useCommand<Record<string, unknown>, unknown>(
    `/c/${company.public_id}/employees/invitations`,
  );

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSent(false);
    const result = await invite.run({ target, position_name: position, rights: cleanRights(rights) });
    if (result !== null) {
      setSent(true);
      setTarget("");
      setRights([]);
      await client.invalidateQueries({ queryKey: ["employees", company.public_id] });
    }
  }

  return (
    <Card title={t("employees.invite_title")}>
      <form className="stack" onSubmit={submit}>
        <div className="row">
          <Field label={t("employees.target")} hint={t("employees.target_hint")}>
            <input value={target} onChange={(e) => setTarget(e.target.value)} />
          </Field>
          <Field label={t("employees.position")}>
            <input value={position} onChange={(e) => setPosition(e.target.value)} />
          </Field>
        </div>
        {can("employee.rights.manage") && catalog.data ? (
          <>
            <h3>{t("employees.rights")}</h3>
            <RightsEditor
              value={rights}
              onChange={setRights}
              catalog={catalog.data}
              locations={locations.data?.locations ?? []}
            />
          </>
        ) : null}
        <ErrorMessage error={invite.error} />
        {sent ? <div className="alert ok">{t("employees.invitation_sent")}</div> : null}
        <div className="actions">
          <button type="submit" disabled={!target.trim() || !position.trim() || invite.pending}>
            {t("employees.invite")}
          </button>
        </div>
      </form>
    </Card>
  );
}

/** Employee card: rights with their sources and scopes, access, journal (§ 5.1.4, § 23.10). */
export function EmployeePage() {
  const { t, i18n } = useTranslation();
  const { id = "" } = useParams();
  const company = useCompany();
  const can = useCan();
  const employee = useApi<{ employee: Employee }>(
    ["employee", company.public_id, id],
    `/c/${company.public_id}/employees/${id}`,
  );
  if (employee.isLoading) return <div className="page"><Loading /></div>;
  const data = employee.data?.employee;
  if (!data) return null;
  return (
    <div className="page">
      <h1>
        {data.user?.photo ? <Avatar file={data.user.photo} name={employeeName(data, t)} size={48} /> : null}{" "}
        {employeeName(data, t)}
      </h1>
      <Card>
        <p>
          {t("employees.position")}: {positionName(data.position, t)} ·{" "}
          {t(`employees.state_${data.state}`)}
        </p>
        {data.user ? (
          <p className="muted small">
            {data.user.public_id}
            {data.user.middle_name ? ` · ${data.user.middle_name}` : ""}
            {data.user.email ? ` · ${data.user.email}` : ""}
            {data.user.phone ? ` · ${data.user.phone}` : ""}
          </p>
        ) : (
          <p className="muted small">{t("employees.no_profile")}</p>
        )}
        {data.started_at ? (
          <p className="small">
            {t("employees.started_at")}: {formatMoment(data.started_at, company.timezone ?? "UTC", i18n.language)}
          </p>
        ) : null}
        {data.work_email || data.work_phone ? (
          <p className="small">
            {t("employees.work_contacts")}: {[data.work_email, data.work_phone].filter(Boolean).join(" · ")}
          </p>
        ) : null}
        {data.departments.length ? (
          <p className="small">
            {t("org.departments")}: {data.departments.map((d) => d.name).join(", ")}
          </p>
        ) : null}
        {data.invitation && data.state === "invited" ? (
          <p className="muted small">
            {t("employees.invitation")}: {t(`invitations.state_${data.invitation.state}`)}
          </p>
        ) : null}
        {data.termination ? (
          <div className="alert warn">
            {t("employees.terminated_at", {
              at: formatMoment(data.termination.at, company.timezone ?? "UTC", i18n.language),
            })}
            {data.termination.reason ? ` · ${data.termination.reason}` : ""}
            <div className="small">{t("employees.terminated_note")}</div>
          </div>
        ) : null}
        <AccessActions employee={data} />
      </Card>
      {can("employee.card.edit") && data.state !== "terminated" ? <CardEditor key={data.version} employee={data} /> : null}
      <ResponsiblesCard key={data.version} employee={data} />
      {data.position.is_top_authority ? (
        <Card>{t("employees.top_authority_note")}</Card>
      ) : can("employee.rights.manage") && (data.state === "active" || data.state === "blocked") ? (
        <RightsCard employee={data} />
      ) : null}
      {!data.position.is_top_authority && can("employee.rights.manage") && (data.state === "active" || data.state === "blocked") ? (
        <DiscountLimitCard employmentId={data.id} />
      ) : null}
      {can("employee.journal.view") ? <JournalCard employmentId={data.id} /> : null}
    </div>
  );
}

function AccessActions({ employee }: { employee: Employee }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const block = useCommand<Record<string, never>, unknown>(
    `/c/${company.public_id}/employees/${employee.id}/block`,
  );
  const restore = useCommand<Record<string, never>, unknown>(
    `/c/${company.public_id}/employees/${employee.id}/restore`,
  );
  const terminate = useCommand<{ reason: string }, unknown>(
    `/c/${company.public_id}/employees/${employee.id}/terminate`,
  );
  const [ending, setEnding] = useState(false);
  const [reason, setReason] = useState("");
  const refresh = async () => {
    await client.invalidateQueries({ queryKey: ["employee", company.public_id, employee.id] });
    await client.invalidateQueries({ queryKey: ["employees", company.public_id] });
    await client.invalidateQueries({ queryKey: ["org", company.public_id] });
  };
  const canEnd =
    (employee.state === "active" || employee.state === "blocked") &&
    can("employee.access.terminate") &&
    !employee.position.is_dedicated;
  return (
    <>
      {ending ? (
        <div className="stack">
          <p className="small">{t("employees.terminate_note")}</p>
          <Field label={t("employees.terminate_reason")}>
            <input value={reason} onChange={(e) => setReason(e.target.value)} />
          </Field>
          <div className="actions">
            <button
              type="button"
              className="danger"
              disabled={terminate.pending}
              onClick={async () => {
                if ((await terminate.run({ reason })) !== null) {
                  setEnding(false);
                  await refresh();
                }
              }}
            >
              {t("employees.terminate_confirm")}
            </button>
            <button type="button" className="secondary" onClick={() => setEnding(false)}>
              {t("common.cancel")}
            </button>
          </div>
        </div>
      ) : null}
      <div className="actions">
        {canEnd && !ending ? (
          <button type="button" className="danger" onClick={() => setEnding(true)}>
            {t("employees.terminate")}
          </button>
        ) : null}
        {employee.state === "active" && can("employee.access.block") ? (
          <button
            type="button"
            className="danger"
            onClick={async () => {
              if ((await block.run({})) !== null) await refresh();
            }}
          >
            {t("employees.block")}
          </button>
        ) : null}
        {employee.state === "blocked" && can("employee.access.restore") ? (
          <button
            type="button"
            onClick={async () => {
              if ((await restore.run({})) !== null) await refresh();
            }}
          >
            {t("employees.restore")}
          </button>
        ) : null}
      </div>
      <ErrorMessage error={block.error ?? restore.error ?? terminate.error} />
    </>
  );
}

/** Position from the handbook, a new one added for reuse, and the work contacts (§ 5.1.4,
 * § 5.1.5, § 5.1.7). The card gives no rights. */
function CardEditor({ employee }: { employee: Employee }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const positions = usePositions();
  const [position, setPosition] = useState(employee.position.id);
  const [newPosition, setNewPosition] = useState("");
  const [workEmail, setWorkEmail] = useState(employee.work_email);
  const [workPhone, setWorkPhone] = useState(employee.work_phone);
  const [saved, setSaved] = useState(false);
  const save = useCommand<Record<string, unknown>, unknown>(
    `/c/${company.public_id}/employees/${employee.id}`,
    "PATCH",
  );
  const dedicated = Boolean(employee.position.is_dedicated);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaved(false);
    const body: Record<string, unknown> = { expected_version: employee.version };
    if (!dedicated && newPosition.trim()) body.position_name = newPosition.trim();
    else if (!dedicated && position !== employee.position.id) body.position_id = position;
    if (workEmail !== employee.work_email) body.work_email = workEmail;
    if (workPhone !== employee.work_phone) body.work_phone = workPhone;
    if (Object.keys(body).length === 1) return;
    if ((await save.run(body)) !== null) {
      setSaved(true);
      setNewPosition("");
      await client.invalidateQueries({ queryKey: ["employee", company.public_id, employee.id] });
      await client.invalidateQueries({ queryKey: ["employees", company.public_id] });
      await client.invalidateQueries({ queryKey: ["positions", company.public_id] });
    }
  }

  return (
    <Card title={t("employees.card")}>
      <form className="stack" onSubmit={submit}>
        <div className="row">
          <Field label={t("employees.position")} hint={dedicated ? t("employees.position_dedicated") : undefined}>
            <select value={position} disabled={dedicated} onChange={(e) => setPosition(e.target.value)}>
              {(positions.data?.positions ?? [])
                .filter((p) => !p.is_dedicated || p.id === employee.position.id)
                .map((p) => (
                  <option key={p.id} value={p.id}>
                    {positionLabel(p, t)}
                  </option>
                ))}
            </select>
          </Field>
          {!dedicated && can("employee.positions.manage") ? (
            <Field label={t("employees.position_new")} hint={t("employees.position_new_hint")}>
              <input value={newPosition} onChange={(e) => setNewPosition(e.target.value)} />
            </Field>
          ) : null}
        </div>
        <div className="row">
          <Field label={t("employees.work_email")} hint={t("employees.work_contacts_hint")}>
            <input type="email" value={workEmail} onChange={(e) => setWorkEmail(e.target.value)} />
          </Field>
          <Field label={t("employees.work_phone")}>
            <input value={workPhone} onChange={(e) => setWorkPhone(e.target.value)} />
          </Field>
        </div>
        <ErrorMessage error={save.error} />
        {saved ? <div className="alert ok">{t("common.saved")}</div> : null}
        <div className="actions">
          <button type="submit" disabled={save.pending}>
            {t("common.save")}
          </button>
        </div>
      </form>
    </Card>
  );
}

/** Responsibles of the employee: employees or departments (§ 5.2.1, § 5.2.4). */
function ResponsiblesCard({ employee }: { employee: Employee }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const org = useOrg();
  const [list, setList] = useState<OrgNode[]>(employee.responsibles);
  const [adding, setAdding] = useState("");
  const [saved, setSaved] = useState(false);
  const save = useCommand<{ responsibles: OrgNode[] }, unknown>(
    `/c/${company.public_id}/employees/${employee.id}/responsibles`,
    "PUT",
  );
  const data = org.data?.org;
  const manage =
    can("org.manage") && employee.state !== "terminated" && !employee.position.is_top_authority;
  const subordinates =
    data?.links.filter((l) => l.responsible.type === "employee" && l.responsible.id === employee.id) ?? [];
  const same = (a: OrgNode, b: OrgNode) => a.id === b.id && a.type === b.type;

  async function submit() {
    setSaved(false);
    if ((await save.run({ responsibles: list })) !== null) {
      setSaved(true);
      await client.invalidateQueries({ queryKey: ["employee", company.public_id, employee.id] });
      await client.invalidateQueries({ queryKey: ["org", company.public_id] });
    }
  }

  return (
    <Card title={t("org.responsibles")}>
      {employee.position.is_top_authority ? <p className="muted small">{t("org.top_note")}</p> : null}
      <ul className="plain">
        {list.map((node) => (
          <li key={`${node.type}:${node.id}`} className="small">
            {node.type === "department" ? <span className="badge">{t("org.department")}</span> : null}{" "}
            {nodeName(data, node, t)}{" "}
            {manage ? (
              <button
                type="button"
                className="secondary small"
                onClick={() => setList(list.filter((x) => !same(x, node)))}
              >
                {t("org.remove")}
              </button>
            ) : null}
          </li>
        ))}
        {list.length === 0 && !employee.position.is_top_authority ? <li className="muted small">—</li> : null}
      </ul>
      {subordinates.length ? (
        <p className="small">
          {t("org.subordinates")}:{" "}
          {subordinates.map((l) => nodeName(data, { type: "employee", id: l.subordinate }, t)).join(", ")}
        </p>
      ) : null}
      <p className="muted small">{t("org.no_rights_note")}</p>
      {manage && data ? (
        <>
          <div className="row">
            <Field label={t("org.add_responsible")}>
              <NodeSelect org={data} value={adding} onChange={setAdding} exclude={`employee:${employee.id}`} />
            </Field>
            <div className="actions">
              <button
                type="button"
                className="secondary"
                disabled={!adding}
                onClick={() => {
                  const node = parseNode(adding);
                  if (node && !list.some((x) => same(x, node))) setList([...list, node]);
                  setAdding("");
                }}
              >
                {t("org.add")}
              </button>
            </div>
          </div>
          <ErrorMessage error={save.error} />
          {saved ? <div className="alert ok">{t("common.saved")}</div> : null}
          <div className="actions">
            <button type="button" disabled={save.pending} onClick={submit}>
              {t("common.save")}
            </button>
          </div>
        </>
      ) : null}
    </Card>
  );
}

function RightsCard({ employee }: { employee: Employee }) {
  const { t } = useTranslation();
  const company = useCompany();
  const client = useQueryClient();
  const catalog = useCatalog();
  const locations = useLocations();
  const [rights, setRights] = useState<RightSpec[]>(
    employee.rights.map(({ type, set, permission, locations: l, contexts, cash_accounts }) => ({
      type,
      set,
      permission,
      locations: l,
      contexts,
      cash_accounts,
    })),
  );
  const [saved, setSaved] = useState(false);
  const save = useCommand<Record<string, unknown>, unknown>(
    `/c/${company.public_id}/employees/${employee.id}/rights`,
    "PUT",
  );

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaved(false);
    const result = await save.run({
      rights: cleanRights(rights),
      expected_version: employee.version,
    });
    if (result !== null) {
      setSaved(true);
      await client.invalidateQueries({ queryKey: ["employee", company.public_id, employee.id] });
      await client.invalidateQueries({ queryKey: ["employees", company.public_id] });
    }
  }

  if (!catalog.data) return <Loading />;
  return (
    <Card title={t("employees.rights")}>
      <form onSubmit={submit}>
        <RightsEditor
          value={rights}
          onChange={setRights}
          catalog={catalog.data}
          locations={locations.data?.locations ?? []}
        />
        <ErrorMessage error={save.error} />
        {saved ? <div className="alert ok">{t("common.saved")}</div> : null}
        <div className="actions">
          <button type="submit" disabled={save.pending}>
            {t("common.save")}
          </button>
        </div>
      </form>
    </Card>
  );
}

export function JournalTable({ entries, timeZone }: { entries: JournalEntry[]; timeZone: string }) {
  const { t, i18n } = useTranslation();
  const show = (value: Record<string, unknown> | null) =>
    value ? Object.entries(value).map(([k, v]) => `${k}: ${JSON.stringify(v)}`).join("; ") : "";
  return (
    <table>
      <thead>
        <tr>
          <th>{t("history.when")}</th>
          <th>{t("history.author")}</th>
          <th>{t("history.action")}</th>
          <th>{t("history.before")}</th>
          <th>{t("history.after")}</th>
        </tr>
      </thead>
      <tbody>
        {entries.map((entry) => (
          <tr key={entry.id}>
            <td className="small">{formatMoment(entry.occurred_at, timeZone, i18n.language)}</td>
            <td className="small">{entry.actor.name || entry.actor.user_id || "—"}</td>
            <td className="small">
              {t(`actions.${entry.action.replaceAll(".", "_")}`, { defaultValue: entry.action })}
              <div className="muted">{entry.object.type}</div>
            </td>
            <td className="small">{entry.details_hidden ? t("history.hidden") : show(entry.before)}</td>
            <td className="small">{entry.details_hidden ? t("history.hidden") : show(entry.after)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function JournalCard({ employmentId }: { employmentId: string }) {
  const { t } = useTranslation();
  const company = useCompany();
  const [section, setSection] = useState("");
  const query = section ? `?section=${encodeURIComponent(section)}` : "";
  const journal = useApi<{ journal: JournalEntry[] }>(
    ["journal", company.public_id, employmentId, section],
    `/c/${company.public_id}/employees/${employmentId}/journal${query}`,
  );
  return (
    <Card title={t("employees.journal")}>
      <Field label={t("history.section")}>
        <select value={section} onChange={(e) => setSection(e.target.value)}>
          <option value="">{t("history.all_sections")}</option>
          {["rental", "order", "payment", "inspection", "vehicle", "client", "tariff", "location", "employee"].map(
            (s) => (
              <option key={s} value={s}>
                {t(`history.section_${s}`)}
              </option>
            ),
          )}
        </select>
      </Field>
      {journal.isLoading ? <Loading /> : null}
      <JournalTable entries={journal.data?.journal ?? []} timeZone={company.timezone ?? "UTC"} />
    </Card>
  );
}
