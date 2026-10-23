import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, NavLink } from "react-router";

import type { User } from "../api/types";
import { UserMenu } from "./UserMenu";

/** Frame of the B2B interface (decision of 24.10.2026): sections in a menu on the left, top to
 * bottom; the user's own menu at the top right. Without a chosen company the menu holds only
 * «My companies». */
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
  return (
    <div className="shell">
      <header className="topbar">
        <Link to="/companies" className="brand">
          c4rsys
        </Link>
        <span className="spacer" />
        <UserMenu user={user} />
      </header>
      <div className="shell-body">
        <aside className="sidebar">
          <nav className="side-nav">
            {sidebar ?? <NavLink to="/companies">{t("nav.my_companies")}</NavLink>}
          </nav>
        </aside>
        <main className="shell-main">{children}</main>
      </div>
    </div>
  );
}
