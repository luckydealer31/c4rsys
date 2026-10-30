/**
 * The interface without connection (ТЗ § 10.4.3): opened when the server cannot be reached and
 * an employee signed in on this device earlier. Only local drafts of inspections are available;
 * rentals, service and other business operations need the connection. Nothing here proves the
 * session: sending a draft needs it to be valid on the server.
 */
import { useEffect, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Link, Route, Routes, useParams } from "react-router";

import { DraftEditor, DraftsPanel } from "../pages/inspections/OfflineDrafts";
import { ScopeContext } from "./hooks";
import type { OfflineProfile } from "./profile";

export function OfflineApp({ profile, onRetry, retrying }: { profile: OfflineProfile; onRetry: () => void; retrying: boolean }) {
  const { t, i18n } = useTranslation();
  useEffect(() => {
    if (profile.language && profile.language !== i18n.language) void i18n.changeLanguage(profile.language);
    if (profile.theme && profile.theme !== "system") document.documentElement.dataset.theme = profile.theme;
  }, [profile.language, profile.theme, i18n]);
  return (
    <div className="shell">
      <header className="topbar">
        <Link to="/" className="brand">
          c4rsys
        </Link>
        <span className="badge warn">{t("offline.no_connection")}</span>
        <span className="spacer" />
        <span className="muted small">{profile.name}</span>
      </header>
      <main className="shell-main">
        <div className="page inspection-page">
          <div className="alert warn">
            {t("offline.app_hint")}{" "}
            <button type="button" className="secondary" disabled={retrying} onClick={onRetry}>
              {t("offline.retry")}
            </button>
          </div>
          <Routes>
            <Route path="/c/:cmp/inspections/drafts/:draftId" element={<CompanyArea profile={profile} draft />} />
            <Route path="/c/:cmp/*" element={<CompanyArea profile={profile} />} />
            <Route path="*" element={<Companies profile={profile} />} />
          </Routes>
        </div>
      </main>
    </div>
  );
}

function Companies({ profile }: { profile: OfflineProfile }) {
  const { t } = useTranslation();
  const companies = profile.companies.filter((c) => c.can_inspect);
  return (
    <>
      <h1>{t("offline.drafts_title")}</h1>
      {companies.length === 0 ? <p className="muted">{t("offline.no_companies")}</p> : null}
      <ul>
        {companies.map((c) => (
          <li key={c.public_id}>
            <Link to={`/c/${c.public_id}/inspections`}>{c.display_name}</Link>
          </li>
        ))}
      </ul>
    </>
  );
}

function CompanyArea({ profile, draft }: { profile: OfflineProfile; draft?: boolean }) {
  const { t } = useTranslation();
  const { cmp = "", draftId = "" } = useParams();
  const company = profile.companies.find((c) => c.public_id === cmp);
  const scope = useMemo(() => ({ userId: profile.user_id, companyId: cmp }), [profile.user_id, cmp]);
  // Only a company opened earlier by this account; drafts of another one are never shown.
  if (!company) return <Companies profile={profile} />;
  const base = `/c/${cmp}`;
  return (
    <ScopeContext.Provider value={scope}>
      <p className="muted small">
        <Link to="/">{t("offline.companies")}</Link> · {company.display_name}
      </p>
      {draft ? (
        <DraftEditor scope={scope} draftId={draftId} base={base} zone={company.timezone} />
      ) : (
        <DraftsPanel scope={scope} base={base} zone={company.timezone} canCreate={company.can_inspect} always />
      )}
    </ScopeContext.Provider>
  );
}
