import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { Avatar } from "../../components/images";
import { Card } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { useZoneLabel } from "../../components/TimeZoneSelect";
import { LocationsEditor } from "../settings/LocationsPage";

/** Section «Company» (decisions of 24.10.2026): the company's card to read — name, logo,
 * profile data (§ 3.1.5) — and its locations below; the profile is changed in the company's
 * settings. */
export function CompanyPage() {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const zoneLabel = useZoneLabel();
  const country = company.country
    ? new Intl.DisplayNames([i18n.language], { type: "region" }).of(company.country)
    : "";
  const facts: [string, string][] = [
    [t("company.legal_name"), company.legal_name],
    [t("company.legal_form"), company.legal_form],
    [t("company.country"), country ?? ""],
    ...company.identifiers.map((item): [string, string] => [item.type, item.value]),
    [t("company.legal_address"), company.legal_address],
    [t("company.actual_address"), company.actual_address],
    [t("company.phone"), company.phone],
    [t("company.email"), company.email],
    [t("company.website"), company.website],
    [t("companies.timezone"), company.timezone ? zoneLabel(company.timezone) : ""],
    [t("companies.main_currency"), company.main_currency ?? ""],
  ];
  return (
    <div className="page">
      {company.setup_completed_at === null && can("company.settings") ? (
        <div className="alert warn">
          {t("company.setup_not_completed")}{" "}
          <Link to={companyPath(company, "/setup")}>{t("nav.setup")}</Link>
        </div>
      ) : null}
      <Card>
        <div className="profile-head">
          <Avatar file={company.logo} name={company.display_name} size={72} square />
          <div>
            <h1 style={{ margin: 0 }}>{company.display_name}</h1>
            <div className="muted">
              {t("companies.company_id")}: <strong>{company.public_id}</strong>
            </div>
          </div>
        </div>
        {company.description ? <p>{company.description}</p> : null}
        <dl className="facts" style={{ marginTop: 16 }}>
          {facts.map(([label, value], index) => (
            <div key={index} style={{ display: "contents" }}>
              <dt>{label}</dt>
              <dd>{value || "—"}</dd>
            </div>
          ))}
        </dl>
        {can("company.settings") ? (
          <div className="actions">
            <Link className="button secondary" to={companyPath(company, "/settings/company")}>
              {t("common.edit")}
            </Link>
          </div>
        ) : null}
      </Card>
      <h2>{t("settings.locations")}</h2>
      <p className="muted">{t("company.locations_hint")}</p>
      <LocationsEditor />
    </div>
  );
}
