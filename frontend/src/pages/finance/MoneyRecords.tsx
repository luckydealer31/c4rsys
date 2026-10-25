import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "../../api/queries";
import type { CashAccount, Payment } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { useCan, useCompany } from "../../layout/CompanyShell";
import { formatMoment } from "../../time";
import { useLocations } from "../settings/LocationsPage";
import { useArticleName, useArticles } from "./Articles";

function useRefreshMoney() {
  const client = useQueryClient();
  const company = useCompany();
  return async () => {
    for (const key of ["cash-accounts", "account-records", "payments", "finance-articles"]) {
      await client.invalidateQueries({ queryKey: [key, company.public_id] });
    }
  };
}

/** A receipt or payout entered by hand, without an order (§ 17.2.2, § 17.2.7, § 17.2.9), in the
 * currency of the chosen account; exchange comes with § 17.1. */
export function MoneyRecordForm({ accounts }: { accounts: CashAccount[] }) {
  const { t } = useTranslation();
  const can = useCan();
  const company = useCompany();
  const articles = useArticles();
  const locations = useLocations();
  const nameOf = useArticleName();
  const refresh = useRefreshMoney();
  const directions = (["in", "out"] as const).filter((d) =>
    can(d === "in" ? "finance.income.record" : "finance.payout"),
  );
  const active = accounts.filter((a) => a.state === "active");
  const empty = {
    direction: directions[0] ?? "out",
    cash_account_id: "",
    amount: "",
    method: "cash",
    article_id: "",
    location_id: "",
    note: "",
  };
  const [chosen, setForm] = useState(empty);
  const record = useCommand<Record<string, unknown>, unknown>(
    `/c/${company.public_id}/money-records`,
  );
  if (!directions.length) return null;

  // The accounts may load after the form: until one is chosen, the first active one is shown.
  const form = { ...chosen, cash_account_id: chosen.cash_account_id || (active[0]?.id ?? "") };
  const account = active.find((a) => a.id === form.cash_account_id);
  const kind = form.direction === "in" ? "income" : "expense";
  const offered = (articles.data?.articles ?? []).filter(
    (a) => a.kind === kind && a.state === "active",
  );

  async function submit(event: FormEvent) {
    event.preventDefault();
    const body = {
      ...form,
      currency: account?.currency ?? "",
      location_id: form.location_id || null,
    };
    if ((await record.run(body)) !== null) {
      setForm({ ...empty, direction: form.direction, cash_account_id: form.cash_account_id });
      await refresh();
    }
  }

  return (
    <Card title={t("money.record_title")}>
      {active.length === 0 ? (
        <p className="muted">{t("money.no_accounts")}</p>
      ) : (
        <form className="stack" onSubmit={submit}>
          <div className="row">
            <Field label={t("money.direction")}>
              <select
                value={form.direction}
                onChange={(e) =>
                  setForm({ ...form, direction: e.target.value as "in" | "out", article_id: "" })
                }
              >
                {directions.map((d) => (
                  <option key={d} value={d}>
                    {t(`money.direction_${d}`)}
                  </option>
                ))}
              </select>
            </Field>
            <Field label={t("money.account")}>
              <select
                value={form.cash_account_id}
                onChange={(e) => setForm({ ...form, cash_account_id: e.target.value })}
              >
                {active.map((a) => (
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
            <Field label={t("money.method")}>
              <select value={form.method} onChange={(e) => setForm({ ...form, method: e.target.value })}>
                <option value="cash">{t("money.method_cash")}</option>
                <option value="noncash">{t("money.method_noncash")}</option>
              </select>
            </Field>
          </div>
          <div className="row">
            <Field label={t("money.article")}>
              <select
                value={form.article_id}
                onChange={(e) => setForm({ ...form, article_id: e.target.value })}
              >
                <option value="">—</option>
                {offered.map((a) => (
                  <option key={a.id} value={a.id}>
                    {nameOf(a)}
                  </option>
                ))}
              </select>
            </Field>
            <Field label={t("money.location")} hint={t("money.location_hint")}>
              <select
                value={form.location_id}
                onChange={(e) => setForm({ ...form, location_id: e.target.value })}
              >
                <option value="">—</option>
                {(locations.data?.locations ?? []).map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label={t("money.note")}>
              <input value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} />
            </Field>
          </div>
          <ErrorMessage error={record.error} />
          <div className="actions">
            <button
              type="submit"
              disabled={!form.amount || !form.article_id || !form.cash_account_id || record.pending}
            >
              {t(form.direction === "in" ? "money.submit_in" : "money.submit_out")}
            </button>
          </div>
        </form>
      )}
    </Card>
  );
}

/** Balance and posted records of one account (§ 17.2.1); the initial balance is entered once
 * as a separate record (§ 22.1.7). */
export function AccountRecords({ account, onClose }: { account: CashAccount; onClose: () => void }) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const articles = useArticles();
  const nameOf = useArticleName();
  const data = useApi<{ cash_account: CashAccount; records: Payment[] }>(
    ["account-records", company.public_id, account.id],
    `/c/${company.public_id}/cash-accounts/${account.id}/records`,
  );
  const articleName = new Map((articles.data?.articles ?? []).map((a) => [a.id, nameOf(a)]));
  const records = data.data?.records ?? [];
  const hasOpening = records.some((r) => r.kind === "opening");

  return (
    <Card title={`${account.name} · ${account.currency}`}>
      <div className="actions" style={{ marginTop: 0 }}>
        <button type="button" className="secondary" onClick={onClose}>
          {t("money.close")}
        </button>
      </div>
      {data.isLoading ? <Loading /> : null}
      {data.data ? (
        <p>
          {t("money.balance")}: <strong>{data.data.cash_account.balance} {account.currency}</strong>
        </p>
      ) : null}
      {data.data && !hasOpening && account.state === "active" && can("finance.cash_account.manage") ? (
        <OpeningForm account={account} />
      ) : null}
      {data.data && records.length === 0 ? <p className="muted">{t("money.no_records")}</p> : null}
      {records.length ? (
        <table>
          <thead>
            <tr>
              <th>{t("payments.number")}</th>
              <th>{t("payments.when")}</th>
              <th>{t("money.what")}</th>
              <th>{t("payments.amount", { currency: account.currency })}</th>
              <th>{t("money.note")}</th>
            </tr>
          </thead>
          <tbody>
            {records.map((r) => (
              <tr key={r.id}>
                <td>№ {r.number}</td>
                <td>{formatMoment(r.posted_at, company.timezone ?? "UTC", i18n.language)}</td>
                <td>
                  {t(`money.kind_${r.kind}`)}
                  {r.article_id ? ` · ${articleName.get(r.article_id) ?? "…"}` : ""}
                  {r.kind === "manual" ? ` · ${t(`money.method_${r.method}`)}` : ""}
                </td>
                <td className={r.direction === "out" ? "negative" : undefined}>
                  {r.direction === "out" ? "−" : "+"}
                  {r.amount}
                </td>
                <td>{r.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </Card>
  );
}

function OpeningForm({ account }: { account: CashAccount }) {
  const { t } = useTranslation();
  const company = useCompany();
  const refresh = useRefreshMoney();
  const [amount, setAmount] = useState("");
  const opening = useCommand<Record<string, string>, unknown>(
    `/c/${company.public_id}/cash-accounts/${account.id}/opening`,
  );

  async function submit(event: FormEvent) {
    event.preventDefault();
    if ((await opening.run({ amount, currency: account.currency })) !== null) {
      setAmount("");
      await refresh();
    }
  }

  return (
    <form className="stack" onSubmit={submit}>
      <Field label={t("money.opening")} hint={t("money.opening_hint")}>
        <input
          inputMode="decimal"
          value={amount}
          onChange={(e) => setAmount(e.target.value.replace(",", "."))}
        />
      </Field>
      <ErrorMessage error={opening.error} />
      <div className="actions">
        <button type="submit" disabled={!amount || opening.pending}>
          {t("money.opening_submit")}
        </button>
      </div>
    </form>
  );
}
