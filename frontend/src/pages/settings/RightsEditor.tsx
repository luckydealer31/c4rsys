import { useTranslation } from "react-i18next";

import type { Location, PermissionCatalog, PermissionSetEntry, RightSpec } from "../../api/types";
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
  "finance.record.relink",
  "finance.record.client",
  "finance.deposit.manage",
];

type Translate = (key: string, options?: Record<string, unknown>) => string;

/** The name of a set: a system set by its translation, an own set by its name (§ 23.1.5). */
export function setName(code: string | undefined, catalog: PermissionCatalog | undefined, t: Translate): string {
  const found = catalog?.sets.find((s) => s.code === code);
  if (found && !found.is_system) return found.name;
  return t(`sets.${code ?? ""}`, { defaultValue: code ?? "" });
}

export function permissionName(code: string, t: Translate): string {
  return t(`permissions.${code.replaceAll(".", "_")}`, { defaultValue: code });
}

/** Permissions grouped by their section, for the selects and checklists. */
export function bySection(catalog: PermissionCatalog): [string, string[]][] {
  const groups = new Map<string, string[]>();
  for (const code of catalog.permissions) {
    const section = catalog.sections?.[code] ?? code.split(".")[0] ?? "";
    groups.set(section, [...(groups.get(section) ?? []), code]);
  }
  return [...groups.entries()];
}

/** Whether a row gives especially broad rights (§ 23.3.5); the server decides the same way. */
export function isBroadRow(row: RightSpec, catalog: PermissionCatalog): boolean {
  if (row.type === "deny") return false;
  if (row.type === "set") {
    if (catalog.broad_sets?.includes(row.set ?? "")) return true;
    const found = catalog.sets.find((s) => s.code === row.set);
    return Boolean(found?.permissions.some((c) => catalog.broad?.includes(c)));
  }
  return Boolean(row.permission && catalog.broad?.includes(row.permission));
}

/** Rights as assignments: a ready set (system or the company's own) or a personal addition /
 * restriction, each with its own scope — the whole company or selected locations (§ 23.1.1–23.1.2,
 * § 23.1.13) and, for finance rights, all cash accounts of the company or selected ones,
 * independent of locations (§ 23.1.11–23.1.12). Contexts are «all» here; narrower lists are
 * available through the API. Archived own sets are offered only to the rows that already hold
 * them. */
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
  const sections = bySection(catalog);
  const assignable = (row: RightSpec): PermissionSetEntry[] =>
    catalog.sets.filter((s) => !s.archived || s.code === row.set);
  const firstSet = catalog.sets.find((s) => !s.archived)?.code ?? "";

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
        ? { type, set: firstSet, ...base }
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
                  <optgroup label={t("permission_sets.system")}>
                    {assignable(row)
                      .filter((s) => s.is_system)
                      .map((set) => (
                        <option key={set.code} value={set.code}>
                          {setName(set.code, catalog, t)}
                        </option>
                      ))}
                  </optgroup>
                  {assignable(row).some((s) => !s.is_system) ? (
                    <optgroup label={t("permission_sets.own")}>
                      {assignable(row)
                        .filter((s) => !s.is_system)
                        .map((set) => (
                          <option key={set.code} value={set.code}>
                            {set.name}
                            {set.archived ? ` (${t("permission_sets.archived")})` : ""}
                          </option>
                        ))}
                    </optgroup>
                  ) : null}
                </select>
              ) : (
                <select
                  value={row.permission}
                  onChange={(e) => update(index, { permission: e.target.value })}
                >
                  {sections.map(([section, codes]) => (
                    <optgroup key={section} label={t(`permission_sections.${section}`, { defaultValue: section })}>
                      {codes.map((code) => (
                        <option key={code} value={code}>
                          {permissionName(code, t)}
                        </option>
                      ))}
                    </optgroup>
                  ))}
                </select>
              )}
            </label>
          </div>
          {isBroadRow(row, catalog) ? (
            <div className="muted small" style={{ marginTop: 4 }}>
              {t("rights.broad_hint")}
            </div>
          ) : null}
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
                  <label key={location.id} style={{ marginLeft: 12, display: "inline-block" }}>
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
                      <label key={account.id} style={{ marginLeft: 12, display: "inline-block" }}>
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
                set: firstSet,
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
