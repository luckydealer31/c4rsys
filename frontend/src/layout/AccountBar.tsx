import { useQueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router";

import { api } from "../api/client";
import type { User } from "../api/types";
import { ME_KEY } from "../auth";

/** Top bar of the B2B interface: the active company (§ 3.3.8), the switch, profile and exit. */
export function AccountBar({ user, children }: { user: User; children?: ReactNode }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const navigate = useNavigate();

  async function logout() {
    await api("/auth/logout", { method: "POST" }).catch(() => undefined);
    // Data of the session is dropped; the user query stays subscribed and becomes empty.
    client.removeQueries({ predicate: (query) => query.queryKey[0] !== ME_KEY[0] });
    client.setQueryData(ME_KEY, null);
    navigate("/login");
  }

  return (
    <header className="topbar">
      <Link to="/companies" className="company">
        c4rsys
      </Link>
      {children}
      <span className="spacer" />
      <Link to="/companies">{t("nav.my_companies")}</Link>
      <Link to="/invitations">{t("nav.invitations")}</Link>
      <Link to="/profile">
        {user.first_name || user.last_name
          ? `${user.first_name} ${user.last_name}`.trim()
          : user.public_id}
      </Link>
      <button type="button" className="secondary" onClick={() => void logout()}>
        {t("nav.logout")}
      </button>
    </header>
  );
}
