import { useCallback, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { useSite } from "../cabinet/CabinetApp";

/** Cloudflare Turnstile on the open forms that send a code (customer decision 02.11.2026).
 * The server gives the public key; without it the check is off and nothing is shown. */

type Turnstile = {
  render: (node: HTMLElement, options: Record<string, unknown>) => string;
  reset: (id: string) => void;
  remove: (id: string) => void;
};

declare global {
  interface Window {
    turnstile?: Turnstile;
  }
}

const SCRIPT =
  "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
let loading: Promise<Turnstile> | null = null;

function loadTurnstile(): Promise<Turnstile> {
  if (window.turnstile) return Promise.resolve(window.turnstile);
  loading ??= new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = SCRIPT;
    script.async = true;
    script.onload = () =>
      window.turnstile
        ? resolve(window.turnstile)
        : reject(new Error("turnstile"));
    script.onerror = () => {
      loading = null;
      reject(new Error("turnstile"));
    };
    document.head.appendChild(script);
  });
  return loading;
}

export function useCaptcha() {
  const { t, i18n } = useTranslation();
  const siteKey = useSite().data?.captcha_site_key ?? null;
  const [token, setToken] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const widget = useRef<string | null>(null);

  const mount = useCallback(
    (node: HTMLDivElement | null) => {
      if (!node) {
        if (widget.current) window.turnstile?.remove(widget.current);
        widget.current = null;
        return;
      }
      if (!siteKey) return;
      loadTurnstile()
        .then((turnstile) => {
          if (!node.isConnected) return;
          widget.current = turnstile.render(node, {
            sitekey: siteKey,
            language: i18n.language,
            callback: (value: string) => {
              setFailed(false);
              setToken(value);
            },
            "expired-callback": () => setToken(null),
            "error-callback": () => {
              setToken(null);
              setFailed(true);
            },
          });
        })
        .catch(() => setFailed(true));
    },
    [siteKey, i18n.language],
  );

  /** A token is good for one request: a new one is needed after each answer of the server. */
  const reset = useCallback(() => {
    setToken(null);
    if (widget.current) window.turnstile?.reset(widget.current);
  }, []);

  return {
    ready: siteKey === null || token !== null,
    body: token ? { captcha: token } : {},
    reset,
    element: siteKey ? (
      <div className="captcha">
        <div ref={mount} />
        {failed && <p className="error">{t("auth.captcha_failed")}</p>}
      </div>
    ) : null,
  };
}
