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
    // Each number with whose it is; without a caption — «Phone» (decision of 14.11.2026).
    [company.phone_label || t("company.phone"), company.phone],
    ...company.extra_phones.map((item): [string, string] => [item.label || t("company.phone"), item.number]),
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
        <div>
          {/* With a logo the card is wide and the logo takes its right part; without one the
              card is narrower (decision of 14.11.2026). */}
          <div style={{ maxWidth: company.logo ? 1000 : 640 }}>
            <Card>
              <div style={{ display: "flex", gap: 20, alignItems: "flex-start" }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="profile-head">
                    {company.logo ? null : <Avatar file={null} name={company.display_name} size={72} square />}
                    <div>
                      <h1 style={{ margin: 0 }}>{company.display_name}</h1>
                      <div className="muted">{company.public_id}</div>
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
                </div>
                <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 12 }}>
                  <div className="actions" style={{ margin: 0 }}>
                    {/* The profile is changed in «Settings», not from the card (decision of 14.11.2026). */}
                    {/* Leaving the company returns to «My companies» (decision of 24.10.2026). */}
                    <IconButton icon="exit" label={t("company.leave")} onClick={() => navigate("/companies")} />
                  </div>
                  {company.logo ? <Avatar file={company.logo} name={company.display_name} size={220} square /> : null}
                </div>
              </div>
            </Card>
          </div>
          {/* The locations go below the card, one after another. */}
          <h2>{t("settings.locations")}</h2>
          <LocationsEditor creation={false} />
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
