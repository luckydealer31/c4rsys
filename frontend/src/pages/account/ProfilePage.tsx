import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { api, ApiError } from "../../api/client";
import type { Session, StartedFlow, User } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { ME_KEY } from "../../auth";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { UI_LANGUAGES } from "../../i18n";

/** Own profile: name (§ 2.1.5–2.1.6), language and theme (§ 2.7), c4rsys ID, sessions (§ 2.3.3). */
export function ProfilePage({ user }: { user: User }) {
  const { t } = useTranslation();
  return (
    <div className="page">
      <h1>{t("profile.title")}</h1>
      <Card>
        <p>
          {t("profile.c4rsys_id")}: <strong>{user.public_id}</strong>
        </p>
        <p className="muted">{user.email}</p>
      </Card>
      <NameCard user={user} />
      <PreferencesCard user={user} />
      <SessionsCard />
    </div>
  );
}

/** A code for a critical action (§ 2.1.1). */
async function requestCode(action: string): Promise<StartedFlow> {
  return api<StartedFlow>("/me/confirmations", { method: "POST", body: { action } });
}

function NameCard({ user }: { user: User }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const firstEntry = !user.first_name && !user.last_name && !user.middle_name;
  const [form, setForm] = useState({
    first_name: user.first_name,
    last_name: user.last_name,
    middle_name: user.middle_name,
  });
  const [flow, setFlow] = useState<StartedFlow | null>(null);
  const [code, setCode] = useState("");
  const [codeError, setCodeError] = useState<ApiError | null>(null);
  const [saved, setSaved] = useState(false);
  const save = useCommand<Record<string, string>, { user: User }>("/me/personal-data");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaved(false);
    if (!firstEntry && !flow) {
      // Changing personal data needs a code; the first entry after registration does not.
      try {
        setFlow(await requestCode("change_personal_data"));
      } catch (caught) {
        setCodeError(caught as ApiError);
      }
      return;
    }
    const body = flow ? { ...form, flow: flow.flow, code } : form;
    const result = await save.run(body);
    if (result) {
      client.setQueryData(ME_KEY, result.user);
      setFlow(null);
      setCode("");
      setSaved(true);
    }
  }

  return (
    <Card title={t("profile.name_title")}>
      {!user.has_full_name ? <div className="alert warn">{t("profile.name_required")}</div> : null}
      <form className="stack" onSubmit={submit}>
        <div className="row">
          <Field label={t("profile.first_name")}>
            <input
              value={form.first_name}
              onChange={(e) => setForm({ ...form, first_name: e.target.value })}
            />
          </Field>
          <Field label={t("profile.last_name")}>
            <input
              value={form.last_name}
              onChange={(e) => setForm({ ...form, last_name: e.target.value })}
            />
          </Field>
          <Field label={t("profile.middle_name")}>
            <input
              value={form.middle_name}
              onChange={(e) => setForm({ ...form, middle_name: e.target.value })}
            />
          </Field>
        </div>
        {flow ? (
          <Field label={t("auth.code")} hint={t("profile.code_for_change")}>
            <input inputMode="numeric" value={code} onChange={(e) => setCode(e.target.value)} />
          </Field>
        ) : null}
        <ErrorMessage error={codeError ?? save.error} />
        {saved ? <div className="alert ok">{t("common.saved")}</div> : null}
        <div className="actions">
          <button type="submit" disabled={save.pending}>
            {flow ? t("auth.confirm") : t("common.save")}
          </button>
        </div>
      </form>
    </Card>
  );
}

function PreferencesCard({ user }: { user: User }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const update = useCommand<Partial<Pick<User, "language" | "theme">>, { user: User }>(
    "/me",
    "PATCH",
  );

  async function change(changes: Partial<Pick<User, "language" | "theme">>) {
    const result = await update.run(changes);
    if (result) client.setQueryData(ME_KEY, result.user);
  }

  return (
    <Card title={t("profile.preferences")}>
      <div className="row">
        <Field label={t("profile.language")}>
          <select value={user.language} onChange={(e) => void change({ language: e.target.value })}>
            <option value="">{t("profile.language_device")}</option>
            {UI_LANGUAGES.map((code) => (
              <option key={code} value={code}>
                {t(`languages.${code}`)}
              </option>
            ))}
          </select>
        </Field>
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
      </div>
      <ErrorMessage error={update.error} />
    </Card>
  );
}

function SessionsCard() {
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const sessions = useQuery({
    queryKey: ["sessions"],
    queryFn: async () => (await api<{ sessions: Session[] }>("/me/sessions")).sessions,
  });
  const endOne = useCommand<{ id: string }, unknown>((body) => `/me/sessions/${body.id}/end`);
  const endAll = useCommand<{ flow: string; code: string }, unknown>("/me/sessions/end-all");
  const [flow, setFlow] = useState<StartedFlow | null>(null);
  const [code, setCode] = useState("");
  const [requestError, setRequestError] = useState<ApiError | null>(null);

  async function finishOne(id: string, current: boolean) {
    if ((await endOne.run({ id })) !== null) {
      if (current) client.setQueryData(ME_KEY, null);
      else void sessions.refetch();
    }
  }

  async function finishAll(event: FormEvent) {
    event.preventDefault();
    if (!flow) {
      try {
        setFlow(await requestCode("end_all_sessions"));
      } catch (caught) {
        setRequestError(caught as ApiError);
      }
      return;
    }
    if ((await endAll.run({ flow: flow.flow, code })) !== null) client.setQueryData(ME_KEY, null);
  }

  const format = (value: string) => new Date(value).toLocaleString(i18n.language);

  return (
    <Card title={t("profile.sessions")}>
      {sessions.isLoading ? <Loading /> : null}
      <table>
        <thead>
          <tr>
            <th>{t("profile.device")}</th>
            <th>{t("profile.signed_in")}</th>
            <th>{t("profile.last_activity")}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {(sessions.data ?? []).map((session) => (
            <tr key={session.id}>
              <td>
                <div>{session.user_agent || "—"}</div>
                <div className="muted small">{session.ip ?? ""}</div>
              </td>
              <td>{format(session.created_at)}</td>
              <td>{format(session.last_activity_at)}</td>
              <td>
                {session.current ? <span className="badge ok">{t("profile.this_device")}</span> : null}{" "}
                <button
                  type="button"
                  className="secondary"
                  onClick={() => void finishOne(session.id, session.current)}
                >
                  {t("profile.end_session")}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <form className="stack" onSubmit={finishAll}>
        {flow ? (
          <Field label={t("auth.code")}>
            <input inputMode="numeric" value={code} onChange={(e) => setCode(e.target.value)} />
          </Field>
        ) : null}
        <ErrorMessage error={requestError ?? endOne.error ?? endAll.error} />
        <div className="actions">
          <button type="submit" className="danger">
            {flow ? t("auth.confirm") : t("profile.end_all")}
          </button>
        </div>
      </form>
    </Card>
  );
}
