import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "../../api/queries";
import type { CashAccount } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { useCan, useCompany } from "../../layout/CompanyShell";
import { currencies } from "../../time";
import { useLocations } from "../settings/LocationsPage";

export function useCashAccounts() {
  const company = useCompany();
  return useApi<{ cash_accounts: CashAccount[] }>(
    ["cash-accounts", company.public_id],
    `/c/${company.public_id}/cash-accounts`,
  );
}

/** Finance: cash desks and accounts (§ 17.2.5, § 22.1.7). Payments come with part 5. */
export function FinancePage() {
  const { t } = useTranslation();
  return (
    <div className="page">
      <h1>{t("nav.finance")}</h1>
      <CashAccountsEditor />
    </div>
  );
}

export function CashAccountsEditor() {
  const { t } = useTranslation();
  const can = useCan();
  const accounts = useCashAccounts();
  const locations = useLocations();
  const names = new Map((locations.data?.locations ?? []).map((l) => [l.id, l.name]));
  return (
    <>
      <Card title={t("cash.title")}>
        {accounts.isLoading ? <Loading /> : null}
        {accounts.data?.cash_accounts.length === 0 ? (
          <p className="muted">{t("cash.none")}</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>{t("cash.name")}</th>
                <th>{t("cash.type")}</th>
                <th>{t("cash.currency")}</th>
                <th>{t("cash.scope")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {(accounts.data?.cash_accounts ?? []).map((account) => (
                <AccountRow
                  key={account.id}
                  account={account}
                  scope={account.location_id ? (names.get(account.location_id) ?? "—") : t("cash.whole_company")}
                  editable={can("finance.cash_account.manage")}
                />
              ))}
            </tbody>
          </table>
        )}
      </Card>
      {can("finance.cash_account.manage") ? <CreateAccount /> : null}
    </>
  );
}

function useRefresh() {
  const client = useQueryClient();
  const company = useCompany();
  return async () => {
    await client.invalidateQueries({ queryKey: ["cash-accounts", company.public_id] });
    // The setup wizard shows readiness from the same data (§ 22.1.12).
    await client.invalidateQueries({ queryKey: ["readiness", company.public_id] });
  };
}

function AccountRow({
  account,
  scope,
  editable,
}: {
  account: CashAccount;
  scope: string;
  editable: boolean;
}) {
  const { t } = useTranslation();
  const company = useCompany();
  const refresh = useRefresh();
  const [name, setName] = useState(account.name);
  const rename = useCommand<Record<string, unknown>, unknown>(
    `/c/${company.public_id}/cash-accounts/${account.id}`,
    "PATCH",
  );
  const archive = useCommand<Record<string, unknown>, unknown>(
    `/c/${company.public_id}/cash-accounts/${account.id}/archive`,
  );

  return (
    <tr>
      <td>
        {editable && account.state === "active" ? (
          <input value={name} onChange={(e) => setName(e.target.value)} />
        ) : (
          account.name
        )}
        <ErrorMessage error={rename.error ?? archive.error} />
      </td>
      <td>{t(`cash.type_${account.type}`)}</td>
      <td>{account.currency}</td>
      <td>{scope}</td>
      <td>
        {account.state === "archived" ? (
          <span className="badge">{t("cash.archived")}</span>
        ) : editable ? (
          <div className="actions" style={{ marginTop: 0 }}>
            <button
              type="button"
              className="secondary"
              disabled={name === account.name || !name.trim()}
              onClick={async () => {
                if ((await rename.run({ name, expected_version: account.version })) !== null)
                  await refresh();
              }}
            >
              {t("common.save")}
            </button>
            <button
              type="button"
              className="secondary"
              onClick={async () => {
                if ((await archive.run({ expected_version: account.version })) !== null)
                  await refresh();
              }}
            >
              {t("cash.archive")}
            </button>
          </div>
        ) : null}
      </td>
    </tr>
  );
}

function CreateAccount() {
  const { t } = useTranslation();
  const company = useCompany();
  const locations = useLocations();
  const refresh = useRefresh();
  const [form, setForm] = useState({
    name: "",
    type: "cash",
    currency: company.main_currency ?? "",
    location_id: "",
  });
  const create = useCommand<Record<string, string>, unknown>(`/c/${company.public_id}/cash-accounts`);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const body: Record<string, string> = { name: form.name, type: form.type, currency: form.currency };
    if (form.location_id) body.location_id = form.location_id;
    if ((await create.run(body)) !== null) {
      setForm({ ...form, name: "" });
      await refresh();
    }
  }

  return (
    <Card title={t("cash.create_title")}>
      <form className="stack" onSubmit={submit}>
        <div className="row">
          <Field label={t("cash.name")}>
            <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </Field>
          <Field label={t("cash.type")}>
            <select value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })}>
              <option value="cash">{t("cash.type_cash")}</option>
              <option value="bank">{t("cash.type_bank")}</option>
            </select>
          </Field>
          <Field label={t("cash.currency")} hint={t("cash.currency_hint")}>
            <select
              value={form.currency}
              onChange={(e) => setForm({ ...form, currency: e.target.value })}
            >
              <option value="">—</option>
              {currencies().map((code) => (
                <option key={code} value={code}>
                  {code}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("cash.scope")}>
            <select
              value={form.location_id}
              onChange={(e) => setForm({ ...form, location_id: e.target.value })}
            >
              <option value="">{t("cash.whole_company")}</option>
              {(locations.data?.locations ?? []).map((location) => (
                <option key={location.id} value={location.id}>
                  {location.name}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <ErrorMessage error={create.error} />
        <div className="actions">
          <button type="submit" disabled={!form.name.trim() || create.pending}>
            {t("cash.create")}
          </button>
        </div>
      </form>
    </Card>
  );
}
