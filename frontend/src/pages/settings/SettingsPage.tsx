import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";

// The rights that open the employee cards and the structure (the server checks the same).
const EMPLOYEE_VIEW = [
  "employee.invite",
  "employee.rights.manage",
  "employee.access.block",
  "employee.access.restore",
  "employee.access.terminate",
  "employee.card.edit",
  "employee.positions.manage",
  "org.manage",
];

/** Settings of the company: its profile and the setup wizard. Employees are a tab of «Contacts»,
 * locations are in «Company», tariffs are in «Rental» (decisions of 24.10.2026). */
export function SettingsPage() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const owner = company.employment?.is_owner;
  const top = company.employment?.is_top_authority;
  const links = [
    ["setup", "/setup", can("company.settings")],
    ["company", "/settings/company", can("company.settings")],
    // § 5.2, § 5.1.5: the organisation structure and the positions handbook.
    ["org", "/settings/org", EMPLOYEE_VIEW.some(can)],
    ["positions", "/settings/positions", EMPLOYEE_VIEW.some(can)],
    ["rental", "/settings/rental", can("company.settings")],
    ["notifications", "/settings/notifications", can("company.settings")],
    ["discounts", "/settings/discounts", can("company.settings")],
    // § 10.3.1, § 10.3.5: templates and policy of inspections (§ 23.3.7).
    ["inspection_templates", "/settings/inspection-templates", can("inspection.policy.manage") || can("inspection.conduct")],
    ["inspection_policy", "/settings/inspection-policy", can("inspection.policy.manage") || can("inspection.conduct")],
    ["export", "/settings/export", can("data.export")],
    // § 3.3.4: owners see the owners; § 3.4.1: shares held in other companies for full rights;
    // § 3.6.1: deactivation for the right or the owners.
    ["owners", "/settings/owners", Boolean(owner || top)],
    ["holdings", "/settings/holdings", Boolean(company.employment?.has_full_rights)],
    ["state", "/settings/state", Boolean(owner || can("company.deactivate"))],
    // § 24.3.2: the journal of privileged sessions of the c4rsys staff.
    ["staff_access", "/settings/staff-access", can("company.security")],
  ] as const;
  return (
    <div className="page">
      <h1>{t("nav.settings")}</h1>
      {links
        .filter(([, , visible]) => visible)
        .map(([key, path]) => (
          <Link key={key} className="list-link" to={companyPath(company, path)}>
            <strong>{t(`settings.${key}`)}</strong>
            <div className="muted small">{t(`settings.${key}_hint`)}</div>
          </Link>
        ))}
    </div>
  );
}
