import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router";

import { api, ApiError } from "../../api/client";
import type { StartedFlow, User } from "../../api/types";
import { ME_KEY } from "../../auth";
import { Card, ErrorMessage, Field } from "../../components/ui";
import { CodeStep } from "./CodeStep";

/** Registration by email: code, cloud password, then the one-time backup code (§ 2.1.2–2.1.3). */
export function RegisterPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const client = useQueryClient();
  const [email, setEmail] = useState("");
  const [flow, setFlow] = useState<StartedFlow | null>(null);
  const [codeConfirmed, setCodeConfirmed] = useState(false);
  const [password, setPassword] = useState("");
  const [backupCode, setBackupCode] = useState<string | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [pending, setPending] = useState(false);

  async function start(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);
    try {
      setFlow(await api<StartedFlow>("/auth/register/start", { method: "POST", body: { email } }));
    } catch (caught) {
      setError(caught as ApiError);
    } finally {
      setPending(false);
    }
  }

  async function complete(event: FormEvent) {
    event.preventDefault();
    if (!flow) return;
    setPending(true);
    setError(null);
    try {
      const result = await api<{ user: User; backup_code: string }>("/auth/register/complete", {
        method: "POST",
        body: { flow: flow.flow, password },
      });
      setUser(result.user);
      setBackupCode(result.backup_code);
    } catch (caught) {
      setError(caught as ApiError);
    } finally {
      setPending(false);
    }
  }

  function finish() {
    client.setQueryData(ME_KEY, user);
    navigate("/profile");
  }

  function download() {
    const blob = new Blob([`c4rsys backup code: ${backupCode ?? ""}\n`], { type: "text/plain" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "c4rsys-backup-code.txt";
    link.click();
    URL.revokeObjectURL(link.href);
  }

  return (
    <div className="narrow">
      <h1>{t("auth.register_title")}</h1>
      <Card>
        {backupCode ? (
          <div className="stack">
            <p>{t("auth.backup_intro")}</p>
            <p className="code">{backupCode}</p>
            <p className="muted small">{t("auth.backup_hint")}</p>
            <div className="actions">
              <button
                type="button"
                className="secondary"
                onClick={() => void navigator.clipboard.writeText(backupCode)}
              >
                {t("auth.copy")}
              </button>
              <button type="button" className="secondary" onClick={download}>
                {t("auth.download")}
              </button>
              <button type="button" className="secondary" onClick={() => window.print()}>
                {t("auth.print")}
              </button>
            </div>
            <p className="muted small">{t("auth.backup_skip_note")}</p>
            <div className="actions">
              <button type="button" onClick={finish}>
                {t("auth.backup_done")}
              </button>
            </div>
          </div>
        ) : !flow ? (
          <form className="stack" onSubmit={start}>
            <Field label={t("auth.email")}>
              <input
                type="email"
                autoComplete="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                autoFocus
              />
            </Field>
            <ErrorMessage error={error} />
            <div className="actions">
              <button type="submit" disabled={pending || !email.trim()}>
                {t("auth.get_code")}
              </button>
            </div>
          </form>
        ) : !codeConfirmed ? (
          <CodeStep
            flow={flow}
            purpose="register"
            contact={email}
            onConfirmed={() => setCodeConfirmed(true)}
          />
        ) : (
          <form className="stack" onSubmit={complete}>
            <Field label={t("auth.new_password")} hint={t("auth.password_rules")}>
              <input
                type="password"
                autoComplete="new-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoFocus
              />
            </Field>
            <ErrorMessage error={error} />
            <div className="actions">
              <button type="submit" disabled={pending || !password}>
                {t("auth.create_account")}
              </button>
            </div>
          </form>
        )}
      </Card>
      <p className="muted">
        {t("auth.have_account")} <Link to="/login">{t("auth.login_link")}</Link>
      </p>
    </div>
  );
}
