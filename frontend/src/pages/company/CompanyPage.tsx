import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router";

import { Icon } from "../../components/icons";
import { Avatar } from "../../components/images";
import { Card, IconButton, Modal } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { CompanyTabs } from "../../layout/CompanyTabs";
import { useZoneLabel } from "../../components/TimeZoneSelect";
import { CreateLocationForm, LocationsEditor } from "../settings/LocationsPage";

/** Tab «About» of the company (decisions of 24.10.2026 and 14.11.2026): the company's card to
 * read on the left half — name, logo and the filled profile data only (§ 3.1.5), with small
 * buttons to edit and to leave — and its locations on the right half. New location and the
 * requests to link stand in the column of actions. */
export function CompanyPage() {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const zoneLabel = useZoneLabel();
  const navigate = useNavigate();
  const [adding, setAdding] = useState(false);
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
  // Only what is filled: no dashes for empty fields (decision of 14.11.2026).
  const filled = facts.filter(([, value]) => value);
  const mayAdd = can("location.create");
  const mayLinks = can("company.link.answer");
  return (
    <div className="page full">
      <CompanyTabs current="about" />
      {company.setup_completed_at === null && can("company.settings") ? (
        <div className="alert warn">
          {t("company.setup_not_completed")}{" "}
          <Link to={companyPath(company, "/setup")}>{t("nav.setup")}</Link>
        </div>
      ) : null}
      <div className={mayAdd || mayLinks ? "split" : ""}>
        <div className="inspections-split">
          <Card>
            <div className="profile-head">
              <Avatar file={company.logo} name={company.display_name} size={72} square />
              <div style={{ flex: 1 }}>
                <h1 style={{ margin: 0 }}>{company.display_name}</h1>
                <div className="muted">
                  {t("companies.company_id")}: <strong>{company.public_id}</strong>
                </div>
              </div>
              <div className="actions" style={{ margin: 0, alignSelf: "flex-start" }}>
                {can("company.settings") ? (
                  <IconButton
                    icon="edit"
                    label={t("common.edit")}
                    onClick={() => navigate(companyPath(company, "/settings/company"))}
                  />
                ) : null}
                {/* Leaving the company returns to «My companies» (decision of 24.10.2026). */}
                <IconButton icon="exit" label={t("company.leave")} onClick={() => navigate("/companies")} />
              </div>
            </div>
            {company.description ? <p>{company.description}</p> : null}
            {filled.length ? (
              <dl className="facts" style={{ marginTop: 16 }}>
                {filled.map(([label, value], index) => (
                  <div key={index} style={{ display: "contents" }}>
                    <dt>{label}</dt>
                    <dd>{value}</dd>
                  </div>
                ))}
              </dl>
            ) : null}
          </Card>
          <div>
            <h2 style={{ marginTop: 0 }}>{t("settings.locations")}</h2>
            <LocationsEditor creation={false} />
          </div>
        </div>
        {mayAdd || mayLinks ? (
          <aside className="side-actions" aria-label={t("vehicles.actions_title")}>
            {mayAdd ? (
              <button type="button" className="secondary" onClick={() => setAdding(true)}>
                <Icon name="plus" size={16} /> {t("locations.create_title")}
              </button>
            ) : null}
            {mayLinks ? (
              <Link className="button secondary" to={companyPath(company, "/link-requests")}>
                {t("links.incoming_title")}
              </Link>
            ) : null}
          </aside>
        ) : null}
      </div>
      {adding ? (
        <Modal wide title={t("locations.create_title")} onClose={() => setAdding(false)}>
          <CreateLocationForm bare onDone={() => setAdding(false)} />
        </Modal>
      ) : null}
    </div>
  );
}
