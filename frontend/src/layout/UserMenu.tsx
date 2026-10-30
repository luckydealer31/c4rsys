import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";

import { api } from "../api/client";
import type { User } from "../api/types";
import { useCommand } from "../api/useCommand";
import { ME_KEY } from "../auth";
import { Icon, type IconName } from "../components/icons";
import { Avatar } from "../components/images";
import { TimeZoneSelect, useZoneCities } from "../components/TimeZoneSelect";
import { useDismiss } from "../components/useDismiss";
import { ErrorMessage } from "../components/ui";
import { UI_LANGUAGES } from "../i18n";
import { clearDatasets } from "../offline/drafts";
import { forgetProfile } from "../offline/profile";
import { deviceTimeZone, formatOffset, zoneOffsetMinutes } from "../time";

/** «Last First» when the name is filled, otherwise the login email (decision of 24.10.2026). */
export function userDisplayName(user: User): string {
  const name = `${user.last_name} ${user.first_name}`.trim();
  return name || user.email || user.public_id;
}

type Preferences = Partial<Pick<User, "language" | "theme" | "timezone">>;

// One button switches the theme in turn (decision of 24.10.2026).
const THEMES: { theme: User["theme"]; icon: IconName }[] = [
  { theme: "light", icon: "sun" },
  { theme: "dark", icon: "moon" },
  { theme: "system", icon: "auto" },
];
// Flags of the interface languages: a PROPOSAL (English is shown with the British flag).
const FLAGS: Record<string, string> = { en: "🇬🇧", ru: "🇷🇺", th: "🇹🇭" };

/** Current time in a zone, refreshed every 30 seconds. */
function useClock(timeZone: string, locale: string): string {
  const format = () =>
    new Intl.DateTimeFormat(locale, { timeZone, hour: "2-digit", minute: "2-digit" }).format(
      new Date(),
    );
  const [value, setValue] = useState(format);
  useEffect(() => {
    setValue(format());
    const timer = window.setInterval(() => setValue(format()), 30_000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [timeZone, locale]);
  return value;
}

/** The user's own menu (decisions of 24.10.2026): name and ID, personal settings of the
 * interface — theme, language, time zone — and exit. Until the user chooses, the language and
 * the time zone are the device's; a choice is saved in the profile for all devices. */
export function UserMenu({ user }: { user: User }) {
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [picker, setPicker] = useState<"language" | "timezone" | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const close = useCallback(() => {
    setOpen(false);
    setPicker(null);
  }, []);
  useDismiss(open, close, root);
  const update = useCommand<Preferences, { user: User }>("/me", "PATCH");
  const zone = user.timezone || deviceTimeZone();
  const clock = useClock(zone, i18n.language);
  const cities = useZoneCities();

  async function change(changes: Preferences) {
    const result = await update.run(changes);
    if (result) client.setQueryData(ME_KEY, result.user);
  }

  async function logout() {
    await api("/auth/logout", { method: "POST" }).catch(() => undefined);
    // Local working data become unavailable (§ 10.4.2): the offline sign-in is forgotten and the
    // saved lists are deleted; unsent drafts stay for the same account's next sign-in (§ 10.4.4).
    forgetProfile();
    await clearDatasets(user.public_id).catch(() => undefined);
    // Data of the session is dropped; the user query stays subscribed and becomes empty.
    client.removeQueries({ predicate: (query) => query.queryKey[0] !== ME_KEY[0] });
    client.setQueryData(ME_KEY, null);
    navigate("/login");
  }

  const name = userDisplayName(user);
  const themeIndex = Math.max(
    THEMES.findIndex((item) => item.theme === user.theme),
    0,
  );
  const theme = THEMES[themeIndex] ?? { theme: "system" as const, icon: "auto" as const };
  const nextTheme = THEMES[(themeIndex + 1) % THEMES.length]?.theme ?? "system";
  const language = (user.language || i18n.language).slice(0, 2);

  return (
    <div className="user-menu" ref={root}>
      <button
        type="button"
        className="user-button"
        aria-expanded={open}
        onClick={() => (open ? close() : setOpen(true))}
      >
        <Avatar file={user.avatar} name={name} />
        <span className="user-name">{name}</span>
      </button>
      {open ? (
        <div className="popup user-panel" role="menu">
          {/* The profile opens from the name of the site; invitations are in «My companies»
              (decision of 24.10.2026). */}
          <div className="popup-head">
            <Avatar file={user.avatar} name={name} size={40} />
            <div>
              <strong>{name}</strong>
              <div className="muted small">{user.public_id}</div>
            </div>
          </div>
          <div className="quick-settings">
            <button
              type="button"
              className="chip-button"
              title={t("profile.theme_switch", { theme: t(`profile.theme_${nextTheme}`) })}
              aria-label={t("profile.theme_switch", { theme: t(`profile.theme_${nextTheme}`) })}
              onClick={() => void change({ theme: nextTheme })}
            >
              <Icon name={theme.icon} size={18} /> {t(`profile.theme_${theme.theme}`)}
            </button>
            <button
              type="button"
              className="chip-button"
              aria-expanded={picker === "language"}
              title={t("profile.language")}
              onClick={() => setPicker(picker === "language" ? null : "language")}
            >
              <span aria-hidden="true">{FLAGS[language] ?? "🌐"}</span> {language.toUpperCase()}
            </button>
            <button
              type="button"
              className="chip-button"
              aria-expanded={picker === "timezone"}
              title={`${formatOffset(zoneOffsetMinutes(zone))} — ${cities(zone)}`}
              onClick={() => setPicker(picker === "timezone" ? null : "timezone")}
            >
              <Icon name="clock" size={18} /> {clock} {formatOffset(zoneOffsetMinutes(zone))}
            </button>
          </div>
          {picker === "language" ? (
            <div className="picker">
              {UI_LANGUAGES.map((code) => (
                <button
                  key={code}
                  type="button"
                  className={`menu-row${language === code ? " active" : ""}`}
                  onClick={() => {
                    setPicker(null);
                    void change({ language: code });
                  }}
                >
                  <span aria-hidden="true">{FLAGS[code]}</span> {t(`languages.${code}`)}
                </button>
              ))}
            </div>
          ) : null}
          {picker === "timezone" ? (
            <div className="picker">
              <TimeZoneSelect
                value={zone}
                onChange={(timezone) => {
                  setPicker(null);
                  void change({ timezone });
                }}
              />
              <small className="muted">{t("profile.timezone_hint")}</small>
            </div>
          ) : null}
          <ErrorMessage error={update.error} />
          <button type="button" className="menu-row" onClick={() => void logout()}>
            <Icon name="logout" /> {t("nav.logout")}
          </button>
        </div>
      ) : null}
    </div>
  );
}
