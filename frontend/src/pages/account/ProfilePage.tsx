import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { api, ApiError } from "../../api/client";
import type { Session, StartedFlow, User } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { ME_KEY } from "../../auth";
import { Avatar, ImagePicker } from "../../components/images";
import { TimeZoneSelect } from "../../components/TimeZoneSelect";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { UI_LANGUAGES } from "../../i18n";
import { deviceTimeZone } from "../../time";

/** Own profile (§ 2.1.4–2.1.6, § 2.2, § 2.3, § 2.7; decisions of 24.10.2026): a card to read,
 * changed by «Edit»; login email and cloud password; interface settings; sessions. */
export function ProfilePage({ user }: { user: User }) {
  const { t } = useTranslation();
  const fullName = [user.last_name, user.first_name, user.middle_name].filter(Boolean).join(" ");
  return (
    <div className="page">
      <h1>{t("profile.title")}</h1>
      <Card>
        <div className="profile-head">
          <Avatar file={user.avatar} name={fullName || user.email || ""} size={72} />
          <div>
            <h2 style={{ margin: 0 }}>{fullName || user.email}</h2>
            <div className="muted">
              {t("profile.c4rsys_id")}: <strong>{user.public_id}</strong>
              {user.username ? ` · @${user.username}` : ""}
            </div>
          </div>
        </div>
      </Card>
      <PersonalCard user={user} />
      <UsernameCard user={user} />
      <LoginEmailCard user={user} />
      <PasswordCard />
      <PreferencesCard user={user} />
      <SessionsCard />
    </div>
  );
}

/** A code for a critical action (§ 2.1.1). */
async function requestCode(action: string): Promise<StartedFlow> {
  return api<StartedFlow>("/me/confirmations", { method: "POST", body: { action } });
}

type Personal = {
  last_name: string;
  first_name: string;
  middle_name: string;
  avatar: string | null;
  birth_date: string;
  contact_email: string;
  contact_phone: string;
  document_type: string;
  document_number: string;
  document_issued_on: string;
  document_expires_on: string;
};

function personalOf(user: User): Personal {
  return {
    last_name: user.last_name,
    first_name: user.first_name,
    middle_name: user.middle_name,
    avatar: user.avatar,
    birth_date: user.birth_date ?? "",
    contact_email: user.contact_email,
    contact_phone: user.contact_phone,
    document_type: user.document_type,
    document_number: user.document_number,
    document_issued_on: user.document_issued_on ?? "",
    document_expires_on: user.document_expires_on ?? "",
  };
}

function formatDate(value: string | null, locale: string): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat(locale, { timeZone: "UTC", dateStyle: "long" }).format(
    new Date(`${value}T00:00:00Z`),
  );
}

const DOCUMENT_TYPES = ["passport", "id_card", "driver_license"] as const;

function PersonalCard({ user }: { user: User }) {
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const firstEntry = !user.first_name && !user.last_name && !user.middle_name;
  const [editing, setEditing] = useState(!user.has_full_name);
  const [form, setForm] = useState<Personal>(personalOf(user));
  const [flow, setFlow] = useState<StartedFlow | null>(null);
  const [code, setCode] = useState("");
  const [codeError, setCodeError] = useState<ApiError | null>(null);
  const [saved, setSaved] = useState(false);
  const save = useCommand<Record<string, unknown>, { user: User }>("/me/personal-data");
  const set = (changes: Partial<Personal>) => setForm({ ...form, ...changes });

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaved(false);
    setCodeError(null);
    if (!firstEntry && !flow) {
      // Changing personal data needs a code; the first entry after registration does not.
      try {
        setFlow(await requestCode("change_personal_data"));
      } catch (caught) {
        setCodeError(caught as ApiError);
      }
      return;
    }
    const body = { ...form, avatar: form.avatar ?? "" };
    const result = await save.run(flow ? { ...body, flow: flow.flow, code } : body);
    if (result) {
      client.setQueryData(ME_KEY, result.user);
      setForm(personalOf(result.user));
      setFlow(null);
      setCode("");
      setSaved(true);
      setEditing(false);
    }
  }

  function cancel() {
    setForm(personalOf(user));
    setFlow(null);
    setCode("");
    setCodeError(null);
    save.clearError();
    setEditing(false);
  }

  if (!editing) {
    const documentType = DOCUMENT_TYPES.includes(user.document_type as never)
      ? t(`profile.document_${user.document_type}`)
      : user.document_type;
    return (
      <Card title={t("profile.personal_title")}>
        {saved ? <div className="alert ok">{t("common.saved")}</div> : null}
        <dl className="facts">
          <dt>{t("profile.full_name")}</dt>
          <dd>
            {[user.last_name, user.first_name, user.middle_name].filter(Boolean).join(" ") || "—"}
          </dd>
          <dt>{t("profile.birth_date")}</dt>
          <dd>{formatDate(user.birth_date, i18n.language)}</dd>
          <dt>{t("profile.contact_email")}</dt>
          <dd>{user.contact_email || "—"}</dd>
          <dt>{t("profile.contact_phone")}</dt>
          <dd>{user.contact_phone || "—"}</dd>
          <dt>{t("profile.document")}</dt>
          <dd>
            {user.document_type || user.document_number
              ? `${documentType} ${user.document_number}`.trim()
              : "—"}
          </dd>
          <dt>{t("profile.document_issued_on")}</dt>
          <dd>{formatDate(user.document_issued_on, i18n.language)}</dd>
          <dt>{t("profile.document_expires_on")}</dt>
          <dd>{formatDate(user.document_expires_on, i18n.language)}</dd>
        </dl>
        <div className="actions">
          <button type="button" className="secondary" onClick={() => setEditing(true)}>
            {t("common.edit")}
          </button>
        </div>
      </Card>
    );
  }

  return (
    <Card title={t("profile.personal_title")}>
      {!user.has_full_name ? <div className="alert warn">{t("profile.name_required")}</div> : null}
      <form className="stack" onSubmit={submit}>
        <Field label={t("profile.photo")}>
          <ImagePicker
            purpose="avatar"
            value={form.avatar}
            name={`${form.last_name} ${form.first_name}`}
            onChange={(avatar) => set({ avatar })}
          />
        </Field>
        <div className="field">
          <span>{t("profile.full_name")}</span>
          <div className="row">
            <input
              aria-label={t("profile.last_name")}
              placeholder={t("profile.last_name")}
              value={form.last_name}
              onChange={(e) => set({ last_name: e.target.value })}
            />
            <input
              aria-label={t("profile.first_name")}
              placeholder={t("profile.first_name")}
              value={form.first_name}
              onChange={(e) => set({ first_name: e.target.value })}
            />
            <input
              aria-label={t("profile.middle_name")}
              placeholder={t("profile.middle_name")}
              value={form.middle_name}
              onChange={(e) => set({ middle_name: e.target.value })}
            />
          </div>
        </div>
        <div className="row">
          <Field label={t("profile.birth_date")}>
            <input
              type="date"
              value={form.birth_date}
              onChange={(e) => set({ birth_date: e.target.value })}
            />
          </Field>
          <Field label={t("profile.contact_email")} hint={t("profile.contact_email_hint")}>
            <input
              type="email"
              value={form.contact_email}
              onChange={(e) => set({ contact_email: e.target.value })}
            />
          </Field>
          <Field label={t("profile.contact_phone")} hint={t("profile.contact_phone_hint")}>
            <input
              type="tel"
              value={form.contact_phone}
              onChange={(e) => set({ contact_phone: e.target.value })}
            />
          </Field>
        </div>
        <div className="row">
          <Field label={t("profile.document_type")}>
            <input
              list="document-types"
              value={form.document_type}
              onChange={(e) => set({ document_type: e.target.value })}
            />
            <datalist id="document-types">
              {DOCUMENT_TYPES.map((type) => (
                <option key={type} value={t(`profile.document_${type}`)} />
              ))}
            </datalist>
          </Field>
          <Field label={t("profile.document_number")}>
            <input
              value={form.document_number}
              onChange={(e) => set({ document_number: e.target.value })}
            />
          </Field>
          <Field label={t("profile.document_issued_on")}>
            <input
              type="date"
              value={form.document_issued_on}
              onChange={(e) => set({ document_issued_on: e.target.value })}
            />
          </Field>
          <Field label={t("profile.document_expires_on")}>
            <input
              type="date"
              value={form.document_expires_on}
              onChange={(e) => set({ document_expires_on: e.target.value })}
            />
          </Field>
        </div>
        {flow ? (
          <Field label={t("auth.code")} hint={t("profile.code_for_change")}>
            <input inputMode="numeric" value={code} onChange={(e) => setCode(e.target.value)} />
          </Field>
        ) : null}
        <ErrorMessage error={codeError ?? save.error} />
        <div className="actions">
          <button type="submit" disabled={save.pending}>
            {flow ? t("auth.confirm") : t("common.save")}
          </button>
          {user.has_full_name ? (
            <button type="button" className="secondary" onClick={cancel}>
              {t("common.cancel")}
            </button>
          ) : null}
        </div>
      </form>
    </Card>
  );
}

/** Optional username (§ 2.2.1–2.2.2): changed at most once in 30 days. */
function UsernameCard({ user }: { user: User }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(user.username ?? "");
  const save = useCommand<{ username: string }, { user: User }>("/me/username", "PUT");

  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await save.run({ username: value });
    if (result) {
      client.setQueryData(ME_KEY, result.user);
      setEditing(false);
    }
  }

  return (
    <Card title={t("profile.username")}>
      {editing ? (
        <form className="stack" onSubmit={submit}>
          <Field label={t("profile.username")} hint={t("profile.username_hint")}>
            <input value={value} onChange={(e) => setValue(e.target.value)} />
          </Field>
          <ErrorMessage error={save.error} />
          <div className="actions">
            <button type="submit" disabled={save.pending}>
              {t("common.save")}
            </button>
            <button type="button" className="secondary" onClick={() => setEditing(false)}>
              {t("common.cancel")}
            </button>
          </div>
        </form>
      ) : (
        <>
          <p>{user.username ? `@${user.username}` : <span className="muted">{t("profile.username_none")}</span>}</p>
          <div className="actions">
            <button type="button" className="secondary" onClick={() => setEditing(true)}>
              {user.username ? t("common.edit") : t("profile.username_set")}
            </button>
          </div>
        </>
      )}
    </Card>
  );
}

/** Login email (§ 2.3.2): a code to the current address, then a code to the new one; all
 * sessions end after the change (§ 2.3.3). */
function LoginEmailCard({ user }: { user: User }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [step, setStep] = useState<"view" | "current" | "new">("view");
  const [first, setFirst] = useState<StartedFlow | null>(null);
  const [second, setSecond] = useState<StartedFlow | null>(null);
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState<ApiError | null>(null);
  const complete = useCommand<{ flow: string; code: string }, unknown>("/me/email/complete");

  async function begin() {
    setError(null);
    try {
      setFirst(await requestCode("change_email"));
      setStep("current");
    } catch (caught) {
      setError(caught as ApiError);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (step === "current" && first) {
      try {
        setSecond(
          await api<StartedFlow>("/me/email/start", {
            method: "POST",
            body: { flow: first.flow, code, email },
          }),
        );
        setCode("");
        setStep("new");
      } catch (caught) {
        setError(caught as ApiError);
      }
      return;
    }
    if (second && (await complete.run({ flow: second.flow, code })) !== null) {
      // All sessions ended, this one included: back to the login screen.
      client.setQueryData(ME_KEY, null);
    }
  }

  return (
    <Card title={t("profile.login_email")}>
      <p>
        <strong>{user.email}</strong>
      </p>
      {step === "view" ? (
        <>
          <p className="muted small">{t("profile.login_email_hint")}</p>
          <ErrorMessage error={error} />
          <div className="actions">
            <button type="button" className="secondary" onClick={() => void begin()}>
              {t("profile.change_email")}
            </button>
          </div>
        </>
      ) : (
        <form className="stack" onSubmit={submit}>
          {step === "current" ? (
            <>
              <Field label={t("profile.new_email")}>
                <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
              </Field>
              <Field label={t("auth.code")} hint={t("profile.code_to_current_email")}>
                <input inputMode="numeric" value={code} onChange={(e) => setCode(e.target.value)} />
              </Field>
            </>
          ) : (
            <Field label={t("auth.code")} hint={t("profile.code_to_new_email", { email })}>
              <input inputMode="numeric" value={code} onChange={(e) => setCode(e.target.value)} />
            </Field>
          )}
          <p className="muted small">{t("profile.change_email_ends_sessions")}</p>
          <ErrorMessage error={error ?? complete.error} />
          <div className="actions">
            <button type="submit" disabled={complete.pending}>
              {t("auth.confirm")}
            </button>
            <button type="button" className="secondary" onClick={() => setStep("view")}>
              {t("common.cancel")}
            </button>
          </div>
        </form>
      )}
    </Card>
  );
}

/** Cloud password (decision of 24.10.2026): the current password and a code to the email;
 * other sessions end. Without the current password — only recovery with the backup code. */
function PasswordCard() {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ current: "", next: "", repeat: "" });
  const [flow, setFlow] = useState<StartedFlow | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<ApiError | null>(null);
  const [done, setDone] = useState<number | null>(null);
  const change = useCommand<Record<string, string>, { ended_sessions: number }>("/me/password");
  const mismatch = form.repeat !== "" && form.next !== form.repeat;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (!flow) {
      try {
        setFlow(await requestCode("change_password"));
      } catch (caught) {
        setError(caught as ApiError);
      }
      return;
    }
    const result = await change.run({
      current_password: form.current,
      new_password: form.next,
      flow: flow.flow,
      code,
    });
    if (result) {
      setDone(result.ended_sessions);
      setOpen(false);
      setForm({ current: "", next: "", repeat: "" });
      setFlow(null);
      setCode("");
    } else {
      // A used code cannot be repeated: a new one is requested on the next attempt.
      setFlow(null);
      setCode("");
    }
  }

  return (
    <Card title={t("profile.password_title")}>
      {done !== null ? (
        <div className="alert ok">{t("profile.password_changed", { ended: done })}</div>
      ) : null}
      {!open ? (
        <div className="actions">
          <button type="button" className="secondary" onClick={() => setOpen(true)}>
            {t("profile.change_password")}
          </button>
        </div>
      ) : (
        <form className="stack" onSubmit={submit}>
          <div className="row">
            <Field label={t("profile.current_password")}>
              <input
                type="password"
                autoComplete="current-password"
                value={form.current}
                onChange={(e) => setForm({ ...form, current: e.target.value })}
              />
            </Field>
            <Field label={t("profile.new_password")}>
              <input
                type="password"
                autoComplete="new-password"
                value={form.next}
                onChange={(e) => setForm({ ...form, next: e.target.value })}
              />
            </Field>
            <Field label={t("profile.repeat_password")}>
              <input
                type="password"
                autoComplete="new-password"
                value={form.repeat}
                onChange={(e) => setForm({ ...form, repeat: e.target.value })}
              />
            </Field>
          </div>
          {mismatch ? <div className="alert warn">{t("profile.passwords_differ")}</div> : null}
          {flow ? (
            <Field label={t("auth.code")} hint={t("profile.code_for_change")}>
              <input inputMode="numeric" value={code} onChange={(e) => setCode(e.target.value)} />
            </Field>
          ) : null}
          <p className="muted small">{t("profile.password_hint")}</p>
          <ErrorMessage error={error ?? change.error} />
          <div className="actions">
            <button
              type="submit"
              disabled={change.pending || !form.current || !form.next || form.next !== form.repeat}
            >
              {flow ? t("auth.confirm") : t("profile.send_code")}
            </button>
            <button type="button" className="secondary" onClick={() => setOpen(false)}>
              {t("common.cancel")}
            </button>
          </div>
        </form>
      )}
    </Card>
  );
}

function PreferencesCard({ user }: { user: User }) {
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const update = useCommand<
    Partial<Pick<User, "language" | "theme" | "timezone">>,
    { user: User }
  >("/me", "PATCH");

  async function change(changes: Partial<Pick<User, "language" | "theme" | "timezone">>) {
    const result = await update.run(changes);
    if (result) client.setQueryData(ME_KEY, result.user);
  }

  return (
    <Card title={t("profile.preferences")}>
      <div className="row">
        <Field label={t("profile.language")}>
          <select
            value={user.language || i18n.language.slice(0, 2)}
            onChange={(e) => void change({ language: e.target.value })}
          >
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
        <Field label={t("profile.timezone")} hint={t("profile.timezone_hint")}>
          <TimeZoneSelect
            value={user.timezone || deviceTimeZone()}
            onChange={(timezone) => void change({ timezone })}
          />
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
