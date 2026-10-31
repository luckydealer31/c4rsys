import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, Navigate, NavLink, Route, Routes, useNavigate } from "react-router";

import { api, ApiError, SESSION_LOST_EVENT } from "../api/client";
import { ErrorMessage, Loading } from "../components/ui";
import { UI_LANGUAGES } from "../i18n";
import { LoginPage } from "../pages/auth/LoginPage";
import { BlocksPage, JournalPage, MembersPage } from "./Admin";
import { STAFF_ME, StaffContext, useStaff, useStaffCan, useStaffMe } from "./common";
import { CompanyCard, DirectoryPage, UserCard } from "./Directory";
import { MergePage, MergesPage } from "./Merges";
import { SessionPage, SessionsPage } from "./Privileged";
import { RecoveryCasePage, RecoveryCasesPage } from "./Recovery";
import { TicketPage, TicketsPage } from "./Tickets";
import { VerificationPage } from "./Verification";

/** The internal interface of the system staff of c4rsys on its own subdomain (ТЗ гл. 24;
 * decision В-11.1): its own sign-in, search of users and companies, tickets, privileged sessions,
 * blocks, the staff and the system journal. It never shows the company interface. */
export function StaffApp() {
  const { t, i18n } = useTranslation();
  const me = useStaffMe();
  const client = useQueryClient();

  useEffect(() => {
    const onLost = () => client.setQueryData(STAFF_ME, null);
    window.addEventListener(SESSION_LOST_EVENT, onLost);
    return () => window.removeEventListener(SESSION_LOST_EVENT, onLost);
  }, [client]);
  const language = me.data?.user.language;
  useEffect(() => {
    if (language && (UI_LANGUAGES as readonly string[]).includes(language)) void i18n.changeLanguage(language);
  }, [language, i18n]);
  const theme = me.data?.user.theme ?? "system";
  useEffect(() => {
    if (theme === "system") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = theme;
  }, [theme]);

  if (me.isLoading) {
    return (
      <div className="narrow">
        <Loading />
      </div>
    );
  }
  if (me.error) {
    // A signed-in account without an active membership (§ 24.1.1).
    return (
      <div className="narrow">
        <h1>{t("staff.title")}</h1>
        <ErrorMessage error={me.error as ApiError} />
        <SignOut />
      </div>
    );
  }
  if (!me.data) {
    return (
      <Routes>
        <Route
          path="*"
          element={
            <LoginPage
              title={t("staff.login_title")}
              allowRegister={false}
              onSignedIn={() => void client.invalidateQueries({ queryKey: STAFF_ME })}
            />
          }
        />
      </Routes>
    );
  }
  return (
    <StaffContext.Provider value={me.data}>
      <StaffShell>
        <Routes>
          <Route path="/" element={<DirectoryPage />} />
          <Route path="/users/:usr" element={<UserCard />} />
          <Route path="/companies/:cmp" element={<CompanyCard />} />
          <Route path="/tickets" element={<TicketsPage />} />
          <Route path="/tickets/:id" element={<TicketPage />} />
          <Route path="/sessions" element={<SessionsPage />} />
          <Route path="/sessions/:id" element={<SessionPage />} />
          <Route path="/blocks" element={<BlocksPage />} />
          <Route path="/merges" element={<MergesPage />} />
          <Route path="/merges/:id" element={<MergePage />} />
          <Route path="/verification" element={<VerificationPage />} />
          <Route path="/recovery" element={<RecoveryCasesPage />} />
          <Route path="/recovery/:id" element={<RecoveryCasePage />} />
          <Route path="/members" element={<MembersPage />} />
          <Route path="/journal" element={<JournalPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </StaffShell>
    </StaffContext.Provider>
  );
}

function SignOut() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const navigate = useNavigate();
  return (
    <button
      type="button"
      className="secondary"
      onClick={async () => {
        await api("/auth/logout", { method: "POST" }).catch(() => undefined);
        client.removeQueries({ predicate: (q) => q.queryKey[0] !== STAFF_ME[0] });
        client.setQueryData(STAFF_ME, null);
        navigate("/");
      }}
    >
      {t("staff.sign_out")}
    </button>
  );
}

function StaffShell({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const me = useStaff();
  const can = useStaffCan();
  const name = [me.user.last_name, me.user.first_name].filter(Boolean).join(" ") || me.user.public_id;
  const items: [string, string, boolean][] = [
    ["/", "staff.nav_directory", can("directory.search")],
    ["/tickets", "staff.nav_tickets", can("tickets.manage")],
    ["/sessions", "staff.nav_sessions", can("privileged.open")],
    ["/blocks", "staff.nav_blocks", can("block.manage")],
    ["/verification", "staff.nav_verification", can("verification.resolve")],
    ["/merges", "staff.nav_merges", can("accounts.merge")],
    ["/recovery", "staff.nav_recovery", can("recovery.manage")],
    ["/members", "staff.nav_members", can("staff.manage")],
    ["/journal", "staff.nav_journal", can("journal.view")],
  ];
  return (
    <div className="staff-shell">
      <header className="topbar staff-topbar">
        <Link to="/" className="brand">
          c4rsys · {t("staff.title")}
        </Link>
        <nav className="staff-nav">
          {items
            .filter(([, , visible]) => visible)
            .map(([to, key]) => (
              <NavLink key={to} to={to} end={to === "/"}>
                {t(key)}
              </NavLink>
            ))}
        </nav>
        <span className="spacer" />
        <span className="muted small">
          {name} · {me.roles.map((r) => t(`staff.role_${r}`, { defaultValue: r })).join(", ") || t("staff.no_roles")}
        </span>
        <SignOut />
      </header>
      <PrivilegedBanner />
      <main className="staff-main">{children}</main>
    </div>
  );
}

/** § 24.3.1: the interface marks an open privileged session all the time. */
function PrivilegedBanner() {
  const { t } = useTranslation();
  const me = useStaff();
  const session = me.privileged_session;
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 15_000);
    return () => window.clearInterval(timer);
  }, []);
  if (!session) return null;
  const left = Math.max(0, Math.round((new Date(session.expires_at).getTime() - now) / 60_000));
  const target = session.company?.display_name ?? session.user?.name ?? "";
  return (
    <div className="alert warn privileged-banner" role="status">
      <strong>{t("staff.banner_title")}</strong> · {target} ·{" "}
      {left > 0 ? t("staff.banner_left", { count: left }) : t("staff.banner_expired")} ·{" "}
      <Link to={`/sessions/${session.id}`}>{t("staff.banner_open")}</Link>
    </div>
  );
}
