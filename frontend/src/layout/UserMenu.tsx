import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router";

import { api } from "../api/client";
import type { User } from "../api/types";
import { useCommand } from "../api/useCommand";
import { ME_KEY } from "../auth";
import { Avatar } from "../components/images";
import { TimeZoneSelect } from "../components/TimeZoneSelect";
import { ErrorMessage, Field } from "../components/ui";
import { UI_LANGUAGES } from "../i18n";

/** «Last First» when the name is filled, otherwise the login email (decision of 24.10.2026). */
export function userDisplayName(user: User): string {
  const name = `${user.last_name} ${user.first_name}`.trim();
  return name || user.email || user.public_id;
}

type Preferences = Partial<Pick<User, "language" | "theme" | "timezone">>;

/** The user's own menu: profile, personal settings of the interface (theme, language, time
 * zone), exit. Settings of a company are not here. */
export function UserMenu({ user }: { user: User }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const update = useCommand<Preferences, { user: User }>("/me", "PATCH");

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => event.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  async function change(changes: Preferences) {
    const result = await update.run(changes);
    if (result) client.setQueryData(ME_KEY, result.user);
  }

  async function logout() {
    await api("/auth/logout", { method: "POST" }).catch(() => undefined);
    // Data of the session is dropped; the user query stays subscribed and becomes empty.
    client.removeQueries({ predicate: (query) => query.queryKey[0] !== ME_KEY[0] });
    client.setQueryData(ME_KEY, null);
    navigate("/login");
  }

  const name = userDisplayName(user);
  return (
    <div className="user-menu" ref={root}>
      <button
        type="button"
        className="user-button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <Avatar file={user.avatar} name={name} />
        <span className="user-name">{name}</span>
      </button>
      {open ? (
        <div className="user-panel" role="menu">
          <div className="muted small">{user.public_id}</div>
          <div className="user-links" onClick={() => setOpen(false)}>
            <Link to="/profile">{t("nav.profile")}</Link>
            <Link to="/companies">{t("nav.my_companies")}</Link>
            <Link to="/invitations">{t("nav.invitations")}</Link>
          </div>
          <div className="stack">
            <Field label={t("profile.theme")}>
              <select
                value={user.theme}
                onChange={(e) => void change({ theme: e.target.value as User["theme"] })}
              >
                {(["system", "light", "dark"] as const).map((theme) => (
                  <option key={theme} value={theme}>
                    {t(`profile.theme_${theme}`)}
                  </option>
                ))}
              </select>
            </Field>
            <Field label={t("profile.language")}>
              <select
                value={user.language}
                onChange={(e) => void change({ language: e.target.value })}
              >
                <option value="">{t("profile.language_device")}</option>
                {UI_LANGUAGES.map((code) => (
                  <option key={code} value={code}>
                    {t(`languages.${code}`)}
                  </option>
                ))}
              </select>
            </Field>
            <Field label={t("profile.timezone")} hint={t("profile.timezone_hint")}>
              <TimeZoneSelect
                value={user.timezone}
                emptyLabel={t("profile.timezone_device")}
                onChange={(timezone) => void change({ timezone })}
              />
            </Field>
          </div>
          <ErrorMessage error={update.error} />
          <div className="actions">
            <button type="button" className="secondary" onClick={() => void logout()}>
              {t("nav.logout")}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
