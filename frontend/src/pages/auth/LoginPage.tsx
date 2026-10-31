import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router";

import { api, ApiError } from "../../api/client";
import type { StartedFlow, User } from "../../api/types";
import { ME_KEY } from "../../auth";
import { Card, ErrorMessage, Field } from "../../components/ui";
import { CodeStep } from "./CodeStep";

/** Two-step login: code to the contact, then the cloud password (§ 2.1.1). Answers do not
 * reveal whether an account exists (§ 24.4.2). The internal interface of the system staff uses
 * the same steps on its own subdomain, without registration (decision В-11.1). */
export function LoginPage({
  onSignedIn,
  title,
  allowRegister = true,
}: { onSignedIn?: () => void; title?: string; allowRegister?: boolean } = {}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const client = useQueryClient();
  const [email, setEmail] = useState("");
  const [flow, setFlow] = useState<StartedFlow | null>(null);
  const [codeConfirmed, setCodeConfirmed] = useState(false);
  const [password, setPassword] = useState("");
  const [error, setError] = useState<ApiError | null>(null);
  const [pending, setPending] = useState(false);

  async function start(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);
    try {
      setFlow(await api<StartedFlow>("/auth/login/start", { method: "POST", body: { email } }));
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
      const result = await api<{ user: User }>("/auth/login/complete", {
        method: "POST",
        body: { flow: flow.flow, password },
      });
      if (onSignedIn) {
        onSignedIn();
        return;
      }
      client.setQueryData(ME_KEY, result.user);
      navigate("/");
    } catch (caught) {
      setError(caught as ApiError);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="narrow">
      <h1>{title ?? t("auth.login_title")}</h1>
      <Card>
        {!flow ? (
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
            purpose="login"
            contact={email}
            onConfirmed={() => setCodeConfirmed(true)}
          />
        ) : (
          <form className="stack" onSubmit={complete}>
            <Field label={t("auth.password")}>
              <input
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoFocus
              />
            </Field>
            <ErrorMessage error={error} />
            <BlockedNotice error={error} />
            <div className="actions">
              <button type="submit" disabled={pending || !password}>
                {t("auth.sign_in")}
              </button>
            </div>
          </form>
        )}
      </Card>
      {allowRegister ? (
        <>
          <p className="muted">
            {t("auth.no_account")} <Link to="/register">{t("auth.register_link")}</Link>
          </p>
          {/* § 2.3.1: recovery through the support when the email or the password is lost. */}
          <p className="muted">
            <Link to="/recover">{t("recovery.link")}</Link>
          </p>
        </>
      ) : null}
    </div>
  );
}

/** § 24.2.2: a blocked account learns the reason that may be disclosed and how to reach the
 * support. */
export function BlockedNotice({ error }: { error: ApiError | null }) {
  const { t } = useTranslation();
  if (!error || (error.code !== "USER_BLOCKED" && error.code !== "COMPANY_BLOCKED")) return null;
  const params = error.params as { support?: string; category?: string; reason?: string };
  const reason = params.reason || (params.category ? t(`staff.block_category_${params.category}`) : "");
  return (
    <div className="alert warn" role="status">
      {reason ? <div>{t("blocked.reason", { reason })}</div> : null}
      <div>{params.support ? t("blocked.support", { support: params.support }) : t("blocked.support_generic")}</div>
    </div>
  );
}
