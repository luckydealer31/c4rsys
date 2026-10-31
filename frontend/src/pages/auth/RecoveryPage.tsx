import { useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router";

import { api, ApiError } from "../../api/client";
import type { StartedFlow } from "../../api/types";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { CodeStep } from "./CodeStep";

type Status = { enabled: boolean; support: string };

function useRecoveryStatus() {
  return useQuery({
    queryKey: ["recovery-status"],
    queryFn: () => api<Status>("/recovery"),
    retry: false,
  });
}

function SupportContact({ support }: { support: string }) {
  const { t } = useTranslation();
  return <p className="muted">{support ? t("recovery.support", { support }) : t("recovery.support_generic")}</p>;
}

/** «I cannot sign in»: a request to the support to recover access (§ 2.3.1). It names the
 * account by its c4rsys ID or username and a known contact; a new email is confirmed by a code
 * but attached only after the check. The answer never tells whether the account exists.
 * Switched off by the platform until the regulation is ready (decision В-11.2). */
export function RecoveryRequestPage() {
  const { t } = useTranslation();
  const status = useRecoveryStatus();
  const [identifier, setIdentifier] = useState("");
  const [known, setKnown] = useState("");
  const [description, setDescription] = useState("");
  const [newEmail, setNewEmail] = useState("");
  const [flow, setFlow] = useState<StartedFlow | null>(null);
  const [emailConfirmed, setEmailConfirmed] = useState(false);
  const [number, setNumber] = useState<number | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [pending, setPending] = useState(false);

  async function sendCode() {
    setPending(true);
    setError(null);
    try {
      setFlow(await api<StartedFlow>("/recovery/email/start", { method: "POST", body: { email: newEmail } }));
    } catch (caught) {
      setError(caught as ApiError);
    } finally {
      setPending(false);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);
    try {
      const result = await api<{ number: number }>("/recovery/requests", {
        method: "POST",
        body: {
          identifier,
          known_contact: known,
          description,
          ...(flow && emailConfirmed ? { flow: flow.flow } : {}),
        },
      });
      setNumber(result.number);
    } catch (caught) {
      setError(caught as ApiError);
    } finally {
      setPending(false);
    }
  }

  if (status.isLoading) return <div className="narrow"><Loading /></div>;
  const support = status.data?.support ?? "";
  return (
    <div className="narrow">
      <h1>{t("recovery.title")}</h1>
      {!status.data?.enabled ? (
        <Card>
          <p>{t("recovery.disabled")}</p>
          <SupportContact support={support} />
        </Card>
      ) : number !== null ? (
        <Card>
          <p>{t("recovery.received", { number })}</p>
          <p className="muted">{t("recovery.received_hint")}</p>
          <SupportContact support={support} />
        </Card>
      ) : (
        <Card>
          <p className="muted">{t("recovery.intro")}</p>
          <form className="stack" onSubmit={submit}>
            <Field label={t("recovery.identifier")} hint={t("recovery.identifier_hint")}>
              <input value={identifier} onChange={(e) => setIdentifier(e.target.value)} autoFocus />
            </Field>
            <Field label={t("recovery.known_contact")} hint={t("recovery.known_contact_hint")}>
              <input value={known} onChange={(e) => setKnown(e.target.value)} />
            </Field>
            <Field label={t("recovery.description")}>
              <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={3} />
            </Field>
            <fieldset className="stack">
              <legend>{t("recovery.new_email")}</legend>
              <p className="muted small">{t("recovery.new_email_hint")}</p>
              {!flow ? (
                <div className="row">
                  <input
                    type="email"
                    value={newEmail}
                    onChange={(e) => setNewEmail(e.target.value)}
                    aria-label={t("recovery.new_email")}
                  />
                  <button type="button" className="secondary" disabled={pending || !newEmail.trim()} onClick={() => void sendCode()}>
                    {t("auth.get_code")}
                  </button>
                </div>
              ) : !emailConfirmed ? (
                <CodeStep flow={flow} purpose="recovery_email" contact={newEmail} onConfirmed={() => setEmailConfirmed(true)} />
              ) : (
                <p className="small">{t("recovery.new_email_confirmed", { email: newEmail })}</p>
              )}
            </fieldset>
            <ErrorMessage error={error} />
            <div className="actions">
              <button type="submit" disabled={pending || !identifier.trim() || !known.trim() || (flow !== null && !emailConfirmed)}>
                {t("recovery.submit")}
              </button>
            </div>
          </form>
        </Card>
      )}
      <p className="muted">
        <Link to="/">{t("auth.login_link")}</Link>
      </p>
    </div>
  );
}

/** The personal link after an approved recovery: a new cloud password and a new backup code
 * shown once (§ 2.3.1). */
export function RecoveryPasswordPage() {
  const { t } = useTranslation();
  const { token = "" } = useParams();
  const link = useQuery({
    queryKey: ["recovery-link", token],
    queryFn: () => api<{ valid: boolean }>(`/recovery/password/${encodeURIComponent(token)}`),
    retry: false,
  });
  const [password, setPassword] = useState("");
  const [backup, setBackup] = useState<string | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);
    try {
      const result = await api<{ backup_code: string }>("/recovery/password", {
        method: "POST",
        body: { token, password },
      });
      setBackup(result.backup_code);
    } catch (caught) {
      setError(caught as ApiError);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="narrow">
      <h1>{t("recovery.password_title")}</h1>
      <Card>
        {link.isLoading ? (
          <Loading />
        ) : backup ? (
          <div className="stack">
            <p>{t("auth.backup_intro")}</p>
            <p className="code">{backup}</p>
            <p className="muted small">{t("auth.backup_hint")}</p>
            <Link to="/">{t("auth.login_link")}</Link>
          </div>
        ) : link.error ? (
          <ErrorMessage error={link.error as ApiError} />
        ) : (
          <form className="stack" onSubmit={submit}>
            <Field label={t("auth.new_password")} hint={t("auth.password_rules")}>
              <input
                type="password"
                autoComplete="new-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoFocus
              />
            </Field>
            <ErrorMessage error={error} />
            <div className="actions">
              <button type="submit" disabled={pending || !password}>
                {t("recovery.password_submit")}
              </button>
            </div>
          </form>
        )}
      </Card>
    </div>
  );
}
