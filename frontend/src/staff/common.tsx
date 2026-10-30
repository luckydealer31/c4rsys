import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { api, ApiError } from "../api/client";
import { useCommand } from "../api/useCommand";
import { ErrorMessage, Field } from "../components/ui";
import { deviceTimeZone, formatMoment } from "../time";

export type PrivilegedSession = {
  id: string;
  target_type: "company" | "user";
  company: { public_id: string; display_name: string } | null;
  user: { public_id: string; name: string } | null;
  reason_category: string;
  reason: string;
  ticket_number: number | null;
  scopes: string[];
  started_at: string;
  expires_at: string;
  ended_at: string | null;
  end_reason: string | null;
  active: boolean;
  notice: string;
  notified_at: string | null;
  sections: string[];
};

export type StaffMe = {
  user: { public_id: string; first_name: string; last_name: string; language: string; theme: string; timezone: string };
  member: { id: string; unit: string };
  roles: string[];
  permissions: string[];
  privileged_session: PrivilegedSession | null;
  catalog: { permissions: string[]; session_reasons: string[]; block_categories: string[]; scopes: string[] };
};

export type Block = {
  id: string;
  target_type: "company" | "user";
  company: { public_id: string; display_name: string } | null;
  user: { public_id: string; name: string } | null;
  category: string;
  comment: string;
  disclosed_reason: string;
  started_at: string;
  ends_at: string | null;
  ended_at: string | null;
  end_kind: string | null;
  end_reason: string;
  notice: string;
  notified_at: string | null;
};

export const STAFF_ME = ["staff-me"] as const;

/** The signed-in member of the system staff; null without a session of this subdomain. */
export function useStaffMe() {
  return useQuery({
    queryKey: STAFF_ME,
    queryFn: async () => {
      try {
        return await api<StaffMe>("/staff/me");
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) return null;
        throw error;
      }
    },
    retry: false,
    networkMode: "always",
  });
}

export const StaffContext = createContext<StaffMe | null>(null);

export function useStaff(): StaffMe {
  const found = useContext(StaffContext);
  if (!found) throw new Error("Outside of the staff interface");
  return found;
}

/** Hides what the member cannot do; the server checks every request. */
export function useStaffCan(): (permission: string) => boolean {
  const me = useStaff();
  return (permission) => me.permissions.includes(permission);
}

export function useWhen() {
  const { i18n } = useTranslation();
  const me = useContext(StaffContext);
  return (value: string | null | undefined) =>
    formatMoment(value, me?.user.timezone || deviceTimeZone(), i18n.language);
}

export function useRefreshStaff() {
  const client = useQueryClient();
  return () => void client.invalidateQueries({ queryKey: STAFF_ME });
}

/** A text asked before an action: the reason of a reveal, a restore or a deferral. */
export function ReasonForm({
  label,
  submit,
  pending,
  error,
  onSubmit,
  onCancel,
}: {
  label: string;
  submit: string;
  pending: boolean;
  error: ApiError | null;
  onSubmit: (reason: string) => void;
  onCancel?: () => void;
}) {
  const { t } = useTranslation();
  const [reason, setReason] = useState("");
  return (
    <form
      className="stack"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit(reason);
      }}
    >
      <Field label={label}>
        <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={2} autoFocus />
      </Field>
      <ErrorMessage error={error} />
      <div className="actions">
        <button type="submit" disabled={pending || !reason.trim()}>
          {submit}
        </button>
        {onCancel ? (
          <button type="button" className="secondary" onClick={onCancel}>
            {t("common.cancel")}
          </button>
        ) : null}
      </div>
    </form>
  );
}

/** The dialog of a system block (§ 24.2.1–24.2.2): category, comment, the reason that may be
 * disclosed, an optional end and a deferral of the notice by its own permission. */
export function BlockForm({
  targetType,
  target,
  onDone,
}: {
  targetType: "company" | "user";
  target: string;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const me = useStaff();
  const can = useStaffCan();
  const [category, setCategory] = useState(me.catalog.block_categories[0] ?? "");
  const [comment, setComment] = useState("");
  const [disclosed, setDisclosed] = useState("");
  const [endsAt, setEndsAt] = useState("");
  const [defer, setDefer] = useState("");
  const command = useCommand<Record<string, unknown>, { block: Block }>("/staff/blocks");
  return (
    <form
      className="stack"
      onSubmit={async (event) => {
        event.preventDefault();
        const done = await command.run({
          target_type: targetType,
          target,
          category,
          comment,
          disclosed_reason: disclosed,
          ends_at: endsAt ? new Date(endsAt).toISOString() : null,
          defer_reason: defer,
        });
        if (done) onDone();
      }}
    >
      <Field label={t("staff.block_category")}>
        <select value={category} onChange={(e) => setCategory(e.target.value)}>
          {me.catalog.block_categories.map((c) => (
            <option key={c} value={c}>
              {t(`staff.block_category_${c}`)}
            </option>
          ))}
        </select>
      </Field>
      <Field label={t("staff.block_comment")} hint={t("staff.block_comment_hint")}>
        <textarea value={comment} onChange={(e) => setComment(e.target.value)} rows={2} />
      </Field>
      <Field label={t("staff.block_disclosed")} hint={t("staff.block_disclosed_hint")}>
        <input value={disclosed} onChange={(e) => setDisclosed(e.target.value)} />
      </Field>
      <Field label={t("staff.block_ends_at")} hint={t("staff.block_ends_at_hint")}>
        <input type="datetime-local" value={endsAt} onChange={(e) => setEndsAt(e.target.value)} />
      </Field>
      {can("block.notice_defer") ? (
        <Field label={t("staff.notice_defer_reason")} hint={t("staff.notice_defer_hint")}>
          <input value={defer} onChange={(e) => setDefer(e.target.value)} />
        </Field>
      ) : null}
      <ErrorMessage error={command.error} />
      <div className="actions">
        <button type="submit" className="danger" disabled={command.pending || !comment.trim()}>
          {t("staff.block_submit")}
        </button>
        <button type="button" className="secondary" onClick={onDone}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}

export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="card">
      <h2>{title}</h2>
      {children}
    </section>
  );
}
