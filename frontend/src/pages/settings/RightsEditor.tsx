import { useTranslation } from "react-i18next";

import type { Location, PermissionCatalog, RightSpec } from "../../api/types";
import { useCashAccounts } from "../finance/FinancePage";

// Permissions whose action is limited by cash accounts (§ 23.1.11, § 23.7.2).
const CASH_PERMISSIONS = [
  "finance.payment.receive",
  "finance.cash_account.manage",
  "finance.account.view",
  "finance.income.record",
  "finance.payout",
  "finance.transfer",
  "finance.correct",
];

/** Rights as assignments: a ready set or a personal addition / restriction, each with its own
 * scope — the whole company or selected locations (§ 23.1.1–23.1.2, § 23.1.13) and, for finance
 * rights, all cash accounts of the company or selected ones, independent of locations
 * (§ 23.1.11–23.1.12). Contexts are «all» here; narrower lists are available through the API. */
export function RightsEditor({
  value,
  onChange,
  catalog,
  locations,
}: {
  value: RightSpec[];
  onChange: (value: RightSpec[]) => void;
  catalog: PermissionCatalog;
  locations: Location[];
}) {
  const { t } = useTranslation();
  const accounts = useCashAccounts().data?.cash_accounts ?? [];

  function permissionsOf(row: RightSpec): string[] {
    if (row.type !== "set") return row.permission ? [row.permission] : [];
    return catalog.sets.find((set) => set.code === row.set)?.permissions ?? [];
  }

  function update(index: number, changes: Partial<RightSpec>) {
    onChange(value.map((row, i) => (i === index ? { ...row, ...changes } : row)));
  }

  function setType(index: number, type: RightSpec["type"]) {
    const base = { locations: "all", contexts: "all", cash_accounts: "all" } as const;
    const row: RightSpec =
      type === "set"
        ? { type, set: catalog.sets[0]?.code ?? "", ...base }
        : { type, permission: catalog.permissions[0] ?? "", ...base };
    onChange(value.map((current, i) => (i === index ? row : current)));
  }

  function toggleLocation(index: number, id: string) {
    const row = value[index];
    if (!row) return;
    const current = row.locations === "all" ? [] : row.locations;
    const next = current.includes(id) ? current.filter((x) => x !== id) : [...current, id];
    update(index, { locations: next.length ? next : "all" });
  }

  function toggleAccount(index: number, id: string) {
    const row = value[index];
    if (!row) return;
    const current = row.cash_accounts === "all" ? [] : row.cash_accounts;
    const next = current.includes(id) ? current.filter((x) => x !== id) : [...current, id];
    update(index, { cash_accounts: next.length ? next : "all" });
  }

  return (
    <div className="stack">
      {value.length === 0 ? <p className="muted small">{t("rights.none")}</p> : null}
      {value.map((row, index) => (
        <div key={index} className="card" style={{ marginBottom: 0 }}>
          <div className="row">
            <label className="field">
              <span>{t("rights.kind")}</span>
              <select
                value={row.type}
                onChange={(e) => setType(index, e.target.value as RightSpec["type"])}
              >
                <option value="set">{t("rights.kind_set")}</option>
                <option value="grant">{t("rights.kind_grant")}</option>
                <option value="deny">{t("rights.kind_deny")}</option>
              </select>
            </label>
            <label className="field">
              <span>{row.type === "set" ? t("rights.set") : t("rights.permission")}</span>
              {row.type === "set" ? (
                <select value={row.set} onChange={(e) => update(index, { set: e.target.value })}>
                  {catalog.sets.map((set) => (
                    <option key={set.code} value={set.code}>
                      {t(`sets.${set.code}`)}
                    </option>
                  ))}
                </select>
              ) : (
                <select
                  value={row.permission}
                  onChange={(e) => update(index, { permission: e.target.value })}
                >
                  {catalog.permissions.map((code) => (
                    <option key={code} value={code}>
                      {t(`permissions.${code.replaceAll(".", "_")}`)}
                    </option>
                  ))}
                </select>
              )}
            </label>
          </div>
          <div className="small" style={{ marginTop: 8 }}>
            {t("rights.scope")}:{" "}
            <label>
              <input
                type="checkbox"
                checked={row.locations === "all"}
                onChange={() => update(index, { locations: row.locations === "all" ? [] : "all" })}
              />{" "}
              {t("rights.whole_company")}
            </label>
            {row.locations !== "all"
              ? locations.map((location) => (
                  <label key={location.id} style={{ marginLeft: 12 }}>
                    <input
                      type="checkbox"
                      checked={row.locations !== "all" && row.locations.includes(location.id)}
                      onChange={() => toggleLocation(index, location.id)}
                    />{" "}
                    {location.name}
                  </label>
                ))
              : null}
          </div>
          {permissionsOf(row).some((code) => CASH_PERMISSIONS.includes(code)) ? (
            <div className="small" style={{ marginTop: 8 }}>
              {t("rights.cash_accounts")}:{" "}
              <label>
                <input
                  type="checkbox"
                  checked={row.cash_accounts === "all"}
                  onChange={() => update(index, { cash_accounts: row.cash_accounts === "all" ? [] : "all" })}
                />{" "}
                {t("rights.all_cash_accounts")}
              </label>
              {row.cash_accounts !== "all"
                ? accounts
                    .filter((a) => a.state === "active" || (row.cash_accounts !== "all" && row.cash_accounts.includes(a.id)))
                    .map((account) => (
                      <label key={account.id} style={{ marginLeft: 12 }}>
                        <input
                          type="checkbox"
                          checked={row.cash_accounts !== "all" && row.cash_accounts.includes(account.id)}
                          onChange={() => toggleAccount(index, account.id)}
                        />{" "}
                        {account.name} · {account.currency}
                      </label>
                    ))
                : null}
              {row.cash_accounts !== "all" ? (
                <div className="muted" style={{ marginTop: 4 }}>
                  {t("rights.cash_accounts_hint")}
                </div>
              ) : null}
            </div>
          ) : null}
          <div className="actions" style={{ marginTop: 8 }}>
            <button
              type="button"
              className="secondary"
              onClick={() => onChange(value.filter((_, i) => i !== index))}
            >
              {t("rights.remove")}
            </button>
          </div>
        </div>
      ))}
      <div className="actions">
        <button
          type="button"
          className="secondary"
          onClick={() =>
            onChange([
              ...value,
              {
                type: "set",
                set: catalog.sets[0]?.code ?? "",
                locations: "all",
                contexts: "all",
                cash_accounts: "all",
              },
            ])
          }
        >
          {t("rights.add")}
        </button>
      </div>
    </div>
  );
}

/** A row without an incomplete list: an emptied list of locations means the whole company, an
 * emptied list of cash accounts means all of them. */
export function cleanRights(value: RightSpec[]): RightSpec[] {
  return value.map((row) => ({
    ...row,
    locations: row.locations === "all" || row.locations.length === 0 ? "all" : row.locations,
    cash_accounts:
      row.cash_accounts === "all" || row.cash_accounts.length === 0 ? "all" : row.cash_accounts,
  }));
}
