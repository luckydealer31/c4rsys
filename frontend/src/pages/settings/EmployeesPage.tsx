import { useQueryClient } from "@tanstack/react-query";
import { DiscountLimitCard } from "../orders/Discounts";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router";

import { useApi } from "../../api/queries";
import type { Employee, JournalEntry, OrgNode, PermissionCatalog, RightSpec, RightsRequest } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { formatMoment } from "../../time";
import { useLocations } from "./LocationsPage";
import { Avatar } from "../../components/images";
import { NodeSelect, nodeName, parseNode, positionLabel, useOrg, usePositions } from "./OrgPage";
import { CodeConfirm } from "../ownership/common";
import { cleanRights, permissionName, RightsEditor, setName } from "./RightsEditor";

// The one-time code of the director granting especially broad rights (§ 2.1.1, § 23.3.5).
export const BROAD_ACTION = "grant_broad_rights";
export const BROAD_CODE_REQUIRED = "BROAD_RIGHTS_CODE_REQUIRED";

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
  const employees = useEmployees();
  const catalog = useCatalog();
  return (
    <>
      <p className="small">
        <Link to={companyPath(company, "/settings/org")}>{t("org.title")}</Link>
        {" · "}
        <Link to={companyPath(company, "/settings/positions")}>{t("positions_page.title")}</Link>
        {" · "}
        <Link to={companyPath(company, "/settings/permission-sets")}>{t("permission_sets.title")}</Link>
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
                    : employee.rights
                        .map((r) => (r.set ? setName(r.set, catalog.data, t) : permissionName(r.permission ?? "", t)))
                        .join(", ") || "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </>
  );
}

/** The invitation of a new employee: opened by the button «New employee» in a window, not shown
 * with the list all the time (customer's decision of 14.11.2026). */
export function InviteForm({ onClose }: { onClose: () => void }) {
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

  async function send(extra: Record<string, string> = {}): Promise<boolean> {
    setSent(false);
    const result = await invite.run({ target, position_name: position, rights: cleanRights(rights), ...extra });
    if (result !== null) {
      setSent(true);
      setTarget("");
      setRights([]);
      await client.invalidateQueries({ queryKey: ["employees", company.public_id] });
    }
    return result !== null;
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    await send();
  }

  return (
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
        {invite.error?.code === BROAD_CODE_REQUIRED ? (
          <>
            <p className="small">{t("rights.broad_code_note")}</p>
            <CodeConfirm
              action={BROAD_ACTION}
              label={t("rights.broad_code_send")}
              onConfirm={(flow, code) => send({ flow, code })}
              error={null}
            />
          </>
        ) : (
          <ErrorMessage error={invite.error} />
        )}
        {sent ? <div className="alert ok">{t("employees.invitation_sent")}</div> : null}
        <div className="actions">
          <button type="submit" disabled={!target.trim() || !position.trim() || invite.pending}>
            {t("employees.invite")}
          </button>
          <button type="button" className="secondary" onClick={onClose}>
            {t(sent ? "common.close" : "common.cancel")}
          </button>
        </div>
      </form>
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
      {can("employee.card.edit") && data.state !== "terminated" ? <CardEditor key={`card-${data.version}`} employee={data} /> : null}
      <ResponsiblesCard key={`responsibles-${data.version}`} employee={data} />
      {data.rights_requests?.length ? <RightsRequests employee={data} /> : null}
      {data.position.is_top_authority ? (
        <Card>{t("employees.top_authority_note")}</Card>
      ) : can("employee.rights.manage") && (data.state === "active" || data.state === "blocked") ? (
        <RightsCard key={`rights-${data.version}`} employee={data} />
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
  const [saved, setSaved] = useState<"saved" | "pending" | null>(null);
  const save = useCommand<Record<string, unknown>, { rights_request: RightsRequest | null }>(
    `/c/${company.public_id}/employees/${employee.id}/rights`,
    "PUT",
  );
  const outdated = employee.rights.filter((r) => r.set && r.set_update);

  async function send(extra: Record<string, string> = {}): Promise<boolean> {
    setSaved(null);
    const result = await save.run({
      rights: cleanRights(rights),
      expected_version: employee.version,
      ...extra,
    });
    if (result !== null) {
      setSaved(result.rights_request ? "pending" : "saved");
      await client.invalidateQueries({ queryKey: ["employee", company.public_id, employee.id] });
      await client.invalidateQueries({ queryKey: ["employees", company.public_id] });
    }
    return result !== null;
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    await send();
  }

  if (!catalog.data) return <Loading />;
  return (
    <Card title={t("employees.rights")}>
      {outdated.length ? (
        <div className="stack" style={{ marginBottom: 12 }}>
          {[...new Set(outdated.map((r) => r.set ?? ""))].map((set) => (
            <SetUpdate key={set} employee={employee} set={set} catalog={catalog.data} />
          ))}
        </div>
      ) : null}
      <form onSubmit={submit}>
        <RightsEditor
          value={rights}
          onChange={setRights}
          catalog={catalog.data}
          locations={locations.data?.locations ?? []}
        />
        {save.error?.code === BROAD_CODE_REQUIRED ? (
          <>
            <p className="small">{t("rights.broad_code_note")}</p>
            <CodeConfirm
              action={BROAD_ACTION}
              label={t("rights.broad_code_send")}
              onConfirm={(flow, code) => send({ flow, code })}
              error={null}
            />
          </>
        ) : (
          <ErrorMessage error={save.error} />
        )}
        {saved === "saved" ? <div className="alert ok">{t("common.saved")}</div> : null}
        {saved === "pending" ? <div className="alert warn">{t("rights.awaiting_confirmation")}</div> : null}
        <div className="actions">
          <button type="submit" disabled={save.pending}>
            {t("common.save")}
          </button>
        </div>
      </form>
    </Card>
  );
}

/** § 23.1.3: the set changed since it was assigned; the update is shown and applied separately. */
function SetUpdate({ employee, set, catalog }: { employee: Employee; set: string; catalog: PermissionCatalog }) {
  const { t } = useTranslation();
  const company = useCompany();
  const client = useQueryClient();
  const rows = employee.rights.filter((r) => r.set === set && r.set_update);
  const added = [...new Set(rows.flatMap((r) => r.set_update?.added ?? []))];
  const removed = [...new Set(rows.flatMap((r) => r.set_update?.removed ?? []))];
  const apply = useCommand<Record<string, unknown>, { rights_requests: RightsRequest[] }>(
    `/c/${company.public_id}/permission-sets/${encodeURIComponent(set)}/apply`,
  );
  const [done, setDone] = useState<"applied" | "pending" | null>(null);

  async function run(extra: Record<string, string> = {}): Promise<boolean> {
    const result = await apply.run({ employment_ids: [employee.id], ...extra });
    if (result !== null) {
      setDone(result.rights_requests.length ? "pending" : "applied");
      await client.invalidateQueries({ queryKey: ["employee", company.public_id, employee.id] });
      await client.invalidateQueries({ queryKey: ["employees", company.public_id] });
    }
    return result !== null;
  }

  return (
    <div className="alert warn">
      <strong>{t("permission_sets.update_title", { set: setName(set, catalog, t) })}</strong>
      {added.length ? (
        <div className="small">
          {t("permission_sets.added")}: {added.map((c) => permissionName(c, t)).join(", ")}
        </div>
      ) : null}
      {removed.length ? (
        <div className="small">
          {t("permission_sets.removed")}: {removed.map((c) => permissionName(c, t)).join(", ")}
        </div>
      ) : null}
      {apply.error?.code === BROAD_CODE_REQUIRED ? (
        <CodeConfirm
          action={BROAD_ACTION}
          label={t("rights.broad_code_send")}
          onConfirm={(flow, code) => run({ flow, code })}
          error={null}
        />
      ) : (
        <>
          <ErrorMessage error={apply.error} />
          <div className="actions">
            <button type="button" disabled={apply.pending} onClick={() => void run()}>
              {t("permission_sets.apply")}
            </button>
          </div>
        </>
      )}
      {done === "pending" ? <div className="small">{t("rights.awaiting_confirmation")}</div> : null}
    </div>
  );
}

/** Especially broad rights waiting for the top-authority director (§ 23.3.5): he confirms with a
 * one-time code or declines; the initiator may withdraw. */
function RightsRequests({ employee }: { employee: Employee }) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const catalog = useCatalog();
  const client = useQueryClient();
  const top = Boolean(company.employment?.is_top_authority);
  const me = company.employment?.id;
  const [note, setNote] = useState("");
  const confirm = useCommand<{ id: string; flow: string; code: string }, unknown>(
    (b) => `/c/${company.public_id}/rights-requests/${b.id}/confirm`,
  );
  const decline = useCommand<{ id: string; reason: string }, unknown>(
    (b) => `/c/${company.public_id}/rights-requests/${b.id}/decline`,
  );
  const cancel = useCommand<{ id: string }, unknown>(
    (b) => `/c/${company.public_id}/rights-requests/${b.id}/cancel`,
  );
  const refresh = async () => {
    await client.invalidateQueries({ queryKey: ["employee", company.public_id, employee.id] });
    await client.invalidateQueries({ queryKey: ["employees", company.public_id] });
  };
  return (
    <Card title={t("rights_requests.title")}>
      {(employee.rights_requests ?? []).map((r) => (
        <div key={r.id} className="stack" style={{ borderBottom: "1px solid var(--border, #ddd)", paddingBottom: 8 }}>
          <div className="small">
            {r.rights.map((x) => (x.set ? setName(x.set, catalog.data, t) : permissionName(x.permission ?? "", t))).join(", ")}
            {" · "}
            {r.rights.some((x) => x.locations !== "all") ? t("rights.some_locations") : t("rights.whole_company")}
          </div>
          <div className="muted small">
            {t("rights_requests.initiated", {
              name: r.initiated_by.name || "—",
              at: formatMoment(r.created_at, company.timezone ?? "UTC", i18n.language),
            })}
            {r.replaces.length ? ` · ${t("rights_requests.set_update")}` : ""}
          </div>
          {top && r.initiated_by.employment_id !== me ? (
            <>
              <CodeConfirm
                action={BROAD_ACTION}
                label={t("rights_requests.confirm")}
                onConfirm={async (flow, code) => {
                  const ok = (await confirm.run({ id: r.id, flow, code })) !== null;
                  if (ok) await refresh();
                  return ok;
                }}
                error={confirm.error}
              />
              <Field label={t("rights_requests.decline_reason")}>
                <input value={note} onChange={(e) => setNote(e.target.value)} />
              </Field>
              <div className="actions">
                <button
                  type="button"
                  className="danger"
                  disabled={decline.pending}
                  onClick={async () => {
                    if ((await decline.run({ id: r.id, reason: note })) !== null) await refresh();
                  }}
                >
                  {t("rights_requests.decline")}
                </button>
              </div>
              <ErrorMessage error={decline.error} />
            </>
          ) : r.initiated_by.employment_id === me ? (
            <div className="actions">
              <button
                type="button"
                className="secondary"
                disabled={cancel.pending}
                onClick={async () => {
                  if ((await cancel.run({ id: r.id })) !== null) await refresh();
                }}
              >
                {t("rights_requests.cancel")}
              </button>
              <ErrorMessage error={cancel.error} />
            </div>
          ) : (
            <p className="muted small">{t("rights_requests.waiting")}</p>
          )}
        </div>
      ))}
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
          <th>{t("history.reason")}</th>
        </tr>
      </thead>
      <tbody>
        {entries.map((entry) => (
          <tr key={entry.id}>
            <td className="small">{formatMoment(entry.occurred_at, timeZone, i18n.language)}</td>
            <td className="small">{entry.actor.name || entry.actor.user_id || "—"}</td>
            <td className="small">
              {t(`actions.${entry.action.replaceAll(".", "_")}`, { defaultValue: entry.action })}
              <div className="muted">
                <ObjectLink type={entry.object.type} id={entry.object.id} />
              </div>
            </td>
            <td className="small">{entry.details_hidden ? t("history.hidden") : show(entry.before)}</td>
            <td className="small">{entry.details_hidden ? t("history.hidden") : show(entry.after)}</td>
            <td className="small">{entry.reason || "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// § 23.10.2 (block 14): a link to the object of a journal entry, where the interface has a page
// for it; the page itself checks the rights (§ 23.10.3).
const JOURNAL_PATHS: Record<string, string> = {
  rental: "/rentals/",
  order: "/orders/",
  vehicle_card: "/vehicles/",
  vehicle: "/vehicles/",
  client: "/clients/",
  counterparty: "/clients/counterparties/",
  employment: "/clients/employees/",
  document: "/documents/",
  task: "/tasks/",
  service_operation: "/service/operations/",
  delivery: "/trips/",
  inspection: "/inspections/",
  location: "/company/locations/",
  tariff: "/rentals/tariffs/",
};

function ObjectLink({ type, id }: { type: string; id: string }) {
  const { t } = useTranslation();
  const company = useCompany();
  const label = t(`history.object_${type}`, { defaultValue: type });
  const path = JOURNAL_PATHS[type];
  return path && id ? <Link to={companyPath(company, `${path}${id}`)}>{label}</Link> : <>{label}</>;
}

const JOURNAL_SECTIONS = [
  "rental",
  "booking",
  "order",
  "payment",
  "inspection",
  "vehicle",
  "client",
  "counterparty",
  "tariff",
  "location",
  "employee",
  "permission_set",
  "service_operation",
  "delivery",
  "document",
  "task",
  "warehouse",
];

/** Filters by period, section and type of action (§ 23.10.2); the period is in local dates of
 * the device and sent with the offset. */
function JournalCard({ employmentId }: { employmentId: string }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const [section, setSection] = useState("");
  const [action, setAction] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const params = new URLSearchParams();
  if (section) params.set("section", section);
  if (action.trim()) params.set("action", action.trim());
  if (from) params.set("from", new Date(`${from}T00:00:00`).toISOString());
  if (to) {
    const end = new Date(`${to}T00:00:00`);
    end.setDate(end.getDate() + 1);
    params.set("to", end.toISOString());
  }
  const query = params.toString() ? `?${params.toString()}` : "";
  const journal = useApi<{ journal: JournalEntry[] }>(
    ["journal", company.public_id, employmentId, query],
    `/c/${company.public_id}/employees/${employmentId}/journal${query}`,
  );
  const entries = journal.data?.journal ?? [];
  const actions = [...new Set(entries.map((e) => e.action))].sort();
  return (
    <Card title={t("employees.journal")}>
      <div className="row">
        <Field label={t("history.from")}>
          <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
        </Field>
        <Field label={t("history.to")}>
          <input type="date" value={to} onChange={(e) => setTo(e.target.value)} />
        </Field>
      </div>
      <div className="row">
        <Field label={t("history.section")}>
          <select value={section} onChange={(e) => { setSection(e.target.value); setAction(""); }}>
            <option value="">{t("history.all_sections")}</option>
            {JOURNAL_SECTIONS.map((s) => (
              <option key={s} value={s}>
                {t(`history.section_${s}`, { defaultValue: s })}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("history.action_type")}>
          <select value={action} onChange={(e) => setAction(e.target.value)}>
            <option value="">{t("history.all_actions")}</option>
            {(action && !actions.includes(action) ? [action, ...actions] : actions).map((a) => (
              <option key={a} value={a}>
                {t(`actions.${a.replaceAll(".", "_")}`, { defaultValue: a })}
              </option>
            ))}
          </select>
        </Field>
      </div>
      {can("employee.journal.export") ? (
        // § 24.4.5: the export is a separate right; the server journals it.
        <div className="actions">
          <a
            className="button secondary"
            href={`/api/c/${company.public_id}/employees/${employmentId}/journal/export${query}`}
          >
            {t("employees.journal_export")}
          </a>
        </div>
      ) : null}
      {journal.isLoading ? <Loading /> : null}
      <div style={{ overflowX: "auto" }}>
        <JournalTable entries={entries} timeZone={company.timezone ?? "UTC"} />
      </div>
      {entries.length >= 200 ? <p className="muted small">{t("history.limit_note")}</p> : null}
    </Card>
  );
}
