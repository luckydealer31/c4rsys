import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError, uploadPages } from "../../api/client";
import { useApi } from "../../api/queries";
import type { CashAccount } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { ErrorMessage, Field, Info, Loading } from "../../components/ui";
import { useCompany } from "../../layout/CompanyShell";
import { useArticleName, useArticles } from "./Articles";

type Draft = {
  id: string;
  file_id: string;
  spent_on: string | null;
  amount: string | null;
  currency: string;
  seller: string;
  purpose: string;
  created_at: string;
  own: boolean;
  author: string;
};

/** Drafts of expenses (customer's decisions of 23.11.2026): a receipt sent to the Telegram bot is
 * read and kept here with its photo. Its author checks it, chooses the cash
 * desk or account and the article, and records the expense by the date of the receipt. An
 * authorized employee — who may pay out — sees the drafts of the others too. A draft changes no
 * money. */
export function ExpenseDrafts({ accounts }: { accounts: CashAccount[] }) {
  const { t } = useTranslation();
  const company = useCompany();
  const drafts = useApi<{ drafts: Draft[] }>(
    ["expense-drafts", company.public_id],
    `/c/${company.public_id}/expense-drafts`,
  );
  // A receipt is added here too (customer's decision of 23.11.2026): from the window of the bot
  // or from the site, the photo is read into a draft the same way.
  const client = useQueryClient();
  const add = useCommand<{ file_id: string }, unknown>(`/c/${company.public_id}/expense-drafts`);
  const [uploading, setUploading] = useState(false);
  const [refused, setRefused] = useState<ApiError | null>(null);
  async function receipt(files: File[]) {
    setRefused(null);
    setUploading(true);
    let id = "";
    try {
      id = (await uploadPages("expense_receipt", files)).id;
    } catch (error) {
      if (error instanceof ApiError) setRefused(error);
      return;
    } finally {
      setUploading(false);
    }
    if ((await add.run({ file_id: id })) !== null) {
      await client.invalidateQueries({ queryKey: ["expense-drafts", company.public_id] });
    }
  }
  if (!drafts.data) return <Loading />;
  const busy = uploading || add.pending;
  return (
    <>
      <p>
        <label className={`button secondary${busy ? " disabled" : ""}`}>
          {busy ? t("drafts.reading") : t("drafts.add")}
          <input
            type="file"
            hidden
            multiple
            aria-label={t("drafts.add")}
            accept="image/png,image/jpeg,image/webp,application/pdf"
            disabled={busy}
            onChange={(e) => {
              const files = [...(e.target.files ?? [])];
              e.target.value = "";
              if (files.length) void receipt(files);
            }}
          />
        </label>
      </p>
      <ErrorMessage error={refused ?? add.error} />
      <p className="small muted">
        {drafts.data.drafts.length ? t("drafts.own") : t("drafts.empty")} <Info text={t("drafts.hint")} />
      </p>
      {drafts.data.drafts.map((draft) => (
        <DraftCard key={draft.id} draft={draft} accounts={accounts.filter((a) => a.state === "active")} />
      ))}
    </>
  );
}

function DraftCard({ draft, accounts }: { draft: Draft; accounts: CashAccount[] }) {
  const { t } = useTranslation();
  const company = useCompany();
  const client = useQueryClient();
  const articles = useArticles();
  const nameOf = useArticleName();
  // The account in the currency of the receipt is offered first.
  const first = accounts.find((a) => a.currency === draft.currency) ?? accounts[0];
  const [chosen, setForm] = useState({
    cash_account_id: "",
    amount: draft.amount ?? "",
    article_id: "",
    // The expense is recorded by the date of the receipt (customer's decision of 23.11.2026).
    spent_on: draft.spent_on ?? "",
    note: [draft.seller, draft.purpose].filter(Boolean).join(" · ").slice(0, 500),
  });
  const form = { ...chosen, cash_account_id: chosen.cash_account_id || (first?.id ?? "") };
  const account = accounts.find((a) => a.id === form.cash_account_id);
  const base = `/c/${company.public_id}/expense-drafts/${draft.id}`;
  const record = useCommand<Record<string, unknown>, unknown>(`${base}/record`);
  const remove = useCommand<Record<string, never>, unknown>(base, "DELETE");
  const offered = (articles.data?.articles ?? []).filter((a) => a.kind === "expense" && a.state === "active");

  async function refresh() {
    for (const key of ["expense-drafts", "cash-accounts", "account-records", "payments"]) {
      await client.invalidateQueries({ queryKey: [key, company.public_id] });
    }
  }

  return (
    <section className="card expense-draft">
      <p>
        <a href={`/api/files/${draft.file_id}`} target="_blank" rel="noreferrer">
          {t("drafts.receipt")}
        </a>
        {" · "}
        {[
          draft.spent_on,
          draft.amount ? `${draft.amount} ${draft.currency}`.trim() : "",
          draft.seller,
          draft.purpose,
        ]
          .filter(Boolean)
          .join(" · ") || t("drafts.nothing_read")}
        {draft.own ? null : <span className="muted"> · {t("drafts.author", { name: draft.author })}</span>}
      </p>
      {accounts.length === 0 ? (
        <p className="muted">{t("money.no_accounts")}</p>
      ) : (
        <div className="row">
          <Field label={t("money.account")}>
            <select value={form.cash_account_id} onChange={(e) => setForm({ ...form, cash_account_id: e.target.value })}>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name} · {a.currency}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("payments.amount", { currency: account?.currency ?? "" })}>
            <input
              inputMode="decimal"
              value={form.amount}
              onChange={(e) => setForm({ ...form, amount: e.target.value.replace(",", ".") })}
            />
          </Field>
          <Field label={t("money.article")}>
            <select value={form.article_id} onChange={(e) => setForm({ ...form, article_id: e.target.value })}>
              <option value="">—</option>
              {offered.map((a) => (
                <option key={a.id} value={a.id}>
                  {nameOf(a)}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("drafts.date")}>
            <input type="date" value={form.spent_on} onChange={(e) => setForm({ ...form, spent_on: e.target.value })} />
          </Field>
          <Field label={t("money.note")}>
            <input value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} />
          </Field>
        </div>
      )}
      {account && draft.currency && account.currency !== draft.currency ? (
        <p className="small muted">{t("drafts.other_currency", { currency: draft.currency })}</p>
      ) : null}
      <ErrorMessage error={record.error ?? remove.error} />
      <div className="actions">
        <button
          type="button"
          disabled={!form.amount || !form.article_id || !form.cash_account_id || record.pending}
          onClick={async () => {
            const body = { ...form, currency: account?.currency ?? "" };
            if ((await record.run(body)) !== null) await refresh();
          }}
        >
          {t("drafts.record")}
        </button>
        <button
          type="button"
          className="link"
          disabled={remove.pending}
          onClick={async () => {
            if ((await remove.run({})) !== null) await refresh();
          }}
        >
          {t("drafts.remove")}
        </button>
      </div>
    </section>
  );
}
