import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router";

import { useApi } from "../../api/queries";
import type { Employee, JournalEntry, PermissionCatalog, RightSpec } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { formatMoment } from "../../time";
import { useLocations } from "./LocationsPage";
import { cleanRights, RightsEditor } from "./RightsEditor";

export function useEmployees() {
  const company = useCompany();
  return useApi<{ employees: Employee[] }>(
    ["employees", company.public_id],
    `/c/${company.public_id}/employees`,
  );
}

function useCatalog() {
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
export function EmployeesPage() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const employees = useEmployees();
  return (
    <div className="page">
      <h1>{t("settings.employees")}</h1>
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
                  <Link to={companyPath(company, `/settings/employees/${employee.id}`)}>
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
    </div>
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
  const { t } = useTranslation();
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
      <h1>{employeeName(data, t)}</h1>
      <Card>
        <p>
          {t("employees.position")}: {positionName(data.position, t)} ·{" "}
          {t(`employees.state_${data.state}`)}
        </p>
        {data.user ? (
          <p className="muted small">
            {data.user.public_id}
            {data.user.email ? ` · ${data.user.email}` : ""}
          </p>
        ) : null}
        {data.invitation && data.state === "invited" ? (
          <p className="muted small">
            {t("employees.invitation")}: {t(`invitations.state_${data.invitation.state}`)}
          </p>
        ) : null}
        <AccessActions employee={data} />
      </Card>
      {data.position.is_top_authority ? (
        <Card>{t("employees.top_authority_note")}</Card>
      ) : can("employee.rights.manage") && data.state !== "invited" ? (
        <RightsCard employee={data} />
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
  const refresh = () =>
    client.invalidateQueries({ queryKey: ["employee", company.public_id, employee.id] });
  return (
    <>
      <div className="actions">
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
      <ErrorMessage error={block.error ?? restore.error} />
    </>
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
