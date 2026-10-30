import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";

import { api, ApiError, SESSION_LOST_EVENT } from "./api/client";
import type { User } from "./api/types";
import { UI_LANGUAGES } from "./i18n";

export const ME_KEY = ["me"] as const;

/** The signed-in user, or null without a session. */
export function useMe() {
  return useQuery({
    queryKey: ME_KEY,
    queryFn: async () => {
      try {
        return (await api<{ user: User }>("/me")).user;
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) return null;
        throw error;
      }
    },
    staleTime: 60_000,
    // Without connection the request fails at once, so the offline interface can open
    // (§ 10.4.3) instead of waiting for the network.
    networkMode: "always",
  });
}

/** Applies the profile's theme and language (§ 2.7.3–2.7.4) and reacts to a lost session. */
export function useProfilePreferences(user: User | null | undefined) {
  const { i18n } = useTranslation();
  const client = useQueryClient();

  useEffect(() => {
    const theme = user?.theme ?? "system";
    if (theme === "system") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = theme;
  }, [user?.theme]);

  useEffect(() => {
    const language = user?.language;
    if (language && (UI_LANGUAGES as readonly string[]).includes(language)) {
      void i18n.changeLanguage(language);
    }
  }, [user?.language, i18n]);

  useEffect(() => {
    const onLost = () => client.setQueryData(ME_KEY, null);
    window.addEventListener(SESSION_LOST_EVENT, onLost);
    return () => window.removeEventListener(SESSION_LOST_EVENT, onLost);
  }, [client]);
}
