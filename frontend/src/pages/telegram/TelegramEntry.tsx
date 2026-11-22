import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../../api/client";
import { ME_KEY } from "../../auth";
import { Loading } from "../../components/ui";

type Proxy = { postEvent?: (name: string, data: string) => void };

/** The door of the window of the Telegram bot (customer's decision of 23.11.2026). Telegram opens
 * `/tg?to=<page>` and gives the launch data in the address after «#»; the server checks them and
 * signs in the profile the Telegram account is bound to; then the page asked for is opened. The
 * script of Telegram is not loaded: the launch data are read from the address itself. */
export function TelegramEntry() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const launch = new URLSearchParams(window.location.hash.slice(1)).get("tgWebAppData") ?? "";
    const asked = new URLSearchParams(window.location.search).get("to") ?? "";
    // Only a page of this site is opened.
    const to = asked.startsWith("/c/") || asked === "/companies" ? asked : "/";
    // The window takes the whole height where Telegram lets a page ask for it.
    try {
      (window as unknown as { TelegramWebviewProxy?: Proxy }).TelegramWebviewProxy?.postEvent?.("web_app_expand", "");
    } catch {
      // The window stays as Telegram opened it.
    }
    let gone = false;
    void (async () => {
      try {
        await api("/telegram/webapp/session", { method: "POST", body: { init_data: launch } });
      } catch {
        if (!gone) setFailed(true);
        return;
      }
      await client.invalidateQueries({ queryKey: ME_KEY });
      // A full load of the page asked for: the session and the lists start anew.
      window.location.replace(to);
    })();
    return () => {
      gone = true;
    };
  }, [client]);
  return (
    <div className="narrow">
      {failed ? (
        <>
          <p role="alert">{t("telegram.entry_failed")}</p>
          <p>
            <a href="/">{t("telegram.entry_login")}</a>
          </p>
        </>
      ) : (
        <Loading />
      )}
    </div>
  );
}
