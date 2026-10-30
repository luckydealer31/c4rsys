import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";

/** Settings of the company: its profile and the setup wizard. Employees are a tab of «Contacts»,
 * locations are in «Company», tariffs are in «Rental» (decisions of 24.10.2026). */
export function SettingsPage() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const links = [
    ["setup", "/setup", can("company.settings")],
    ["company", "/settings/company", can("company.settings")],
    ["rental", "/settings/rental", can("company.settings")],
    ["notifications", "/settings/notifications", can("company.settings")],
    ["discounts", "/settings/discounts", can("company.settings")],
    // § 10.3.1, § 10.3.5: templates and policy of inspections (§ 23.3.7).
    ["inspection_templates", "/settings/inspection-templates", can("inspection.policy.manage") || can("inspection.conduct")],
    ["inspection_policy", "/settings/inspection-policy", can("inspection.policy.manage") || can("inspection.conduct")],
    ["export", "/settings/export", can("data.export")],
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
