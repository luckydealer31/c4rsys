import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, NavLink } from "react-router";

import type { User } from "../api/types";
import { Icon, type IconName } from "../components/icons";
import { useOnline } from "../offline/hooks";
import { NotificationBell } from "../pages/notifications/NotificationsPage";
import { UserMenu } from "./UserMenu";

/** An item of the left menu: an icon with its name shown on hover (decisions of 24.10.2026). */
export function RailLink({ to, icon, label }: { to: string; icon: IconName; label: string }) {
  return (
    <NavLink to={to} className="rail-item" aria-label={label} data-tip={label}>
      <Icon name={icon} size={22} />
    </NavLink>
  );
}

/** Frame of the B2B interface: sections in a menu on the left, top to bottom; the user's own
 * menu at the top right. Without a chosen company the menu holds only «My companies». */
export function AppShell({
  user,
  sidebar,
  children,
}: {
  user: User;
  sidebar?: ReactNode;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const online = useOnline();
  return (
    <div className="shell">
      <header className="topbar">
        {/* The name of the site opens the profile (decision of 24.10.2026). */}
        <Link to="/profile" className="brand">
          c4rsys
        </Link>
        <span className="spacer" />
        <NotificationBell />
        <UserMenu user={user} />
      </header>
      <div className="shell-body">
        <aside className="sidebar">
          <nav className="rail">
            {sidebar ?? <RailLink to="/companies" icon="companies" label={t("nav.my_companies")} />}
          </nav>
        </aside>
        <main className="shell-main">
          {/* § 10.4.3: without connection only local drafts of inspections are kept. */}
          {online ? null : <div className="alert warn offline-banner">{t("offline.banner")}</div>}
          {children}
        </main>
      </div>
    </div>
  );
}
