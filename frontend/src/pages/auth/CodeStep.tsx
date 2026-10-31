import { useEffect, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { api, ApiError } from "../../api/client";
import type { StartedFlow } from "../../api/types";
import { ErrorMessage, Field } from "../../components/ui";

/** Entering a one-time code: 3 minutes, 5 attempts, a new code after 60 s (§ 2.4.1). */
export function CodeStep({
  flow,
  purpose,
  contact,
  onConfirmed,
}: {
  flow: StartedFlow;
  purpose: "register" | "login" | "recovery_email";
  contact: string;
  onConfirmed: () => void;
}) {
  const { t } = useTranslation();
  const [code, setCode] = useState("");
  const [error, setError] = useState<ApiError | null>(null);
  const [pending, setPending] = useState(false);
  const [wait, setWait] = useState(flow.resend_after_seconds);

  useEffect(() => {
    if (wait <= 0) return;
    const timer = window.setTimeout(() => setWait((value) => value - 1), 1000);
    return () => window.clearTimeout(timer);
  }, [wait]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);
    try {
      const path = purpose === "recovery_email" ? "/recovery/email/verify" : `/auth/${purpose}/verify`;
      await api(path, { method: "POST", body: { flow: flow.flow, code } });
      onConfirmed();
    } catch (caught) {
      setError(caught as ApiError);
    } finally {
      setPending(false);
    }
  }

  async function resend() {
    setError(null);
    try {
      const started = await api<StartedFlow>("/auth/code/resend", {
        method: "POST",
        body: { flow: flow.flow, purpose },
      });
      setWait(started.resend_after_seconds);
    } catch (caught) {
      setError(caught as ApiError);
    }
  }

  return (
    <form className="stack" onSubmit={submit}>
      <p>{t("auth.code_sent", { contact })}</p>
      <Field label={t("auth.code")}>
        <input
          inputMode="numeric"
          autoComplete="one-time-code"
          value={code}
          onChange={(event) => setCode(event.target.value)}
          autoFocus
        />
      </Field>
      <ErrorMessage error={error} />
      <div className="actions">
        <button type="submit" disabled={pending || !code.trim()}>
          {t("auth.confirm")}
        </button>
        <button type="button" className="secondary" disabled={wait > 0} onClick={resend}>
          {wait > 0 ? t("auth.resend_in", { seconds: wait }) : t("auth.resend")}
        </button>
      </div>
    </form>
  );
}
