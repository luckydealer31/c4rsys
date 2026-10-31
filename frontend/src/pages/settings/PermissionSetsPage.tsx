import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "../../api/queries";
import type { PermissionCatalog, PermissionSetEntry, RightsRequest, SetHolders } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { useCan, useCompany } from "../../layout/CompanyShell";
import { CodeConfirm } from "../ownership/common";
import { BROAD_ACTION, BROAD_CODE_REQUIRED, useCatalog } from "./EmployeesPage";
import { bySection, permissionName, setName } from "./RightsEditor";

/** Permission sets: the seven system sets and the company's own (§ 23.1.5, § 23.1.14). An own set
 * is created empty or from another set, changed and archived with «Manage permission sets»
 * (§ 23.3.9); none of this changes anybody's access. A changed set is applied to its holders
 * separately, after showing what is added and removed (§ 23.1.3, § 23.1.15). */
export function PermissionSetsPage() {
  const { t } = useTranslation();
  const can = useCan();
  const catalog = useCatalog();
  const [open, setOpen] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  if (catalog.isLoading) return <div className="page"><Loading /></div>;
  const data = catalog.data;
  if (!data) return <div className="page"><ErrorMessage error={null} /></div>;
  const manage = can("employee.sets.manage");
  return (
    <div className="page">
      <h1>{t("permission_sets.title")}</h1>
      <p className="muted small">{t("permission_sets.intro")}</p>
      {manage ? (
        creating ? (
          <SetForm catalog={data} onDone={() => setCreating(false)} />
        ) : (
          <div className="actions">
            <button type="button" onClick={() => setCreating(true)}>
              {t("permission_sets.create")}
            </button>
          </div>
        )
      ) : null}
      {(["system", "own"] as const).map((group) => {
        const items = data.sets.filter((s) => (group === "system") === s.is_system);
        if (!items.length) return null;
        return (
          <Card key={group} title={t(`permission_sets.${group}`)}>
            {items.map((set) => (
              <div key={set.code} style={{ borderBottom: "1px solid var(--border, #ddd)", padding: "8px 0" }}>
                <button
                  type="button"
                  className="link"
                  aria-expanded={open === set.code}
                  onClick={() => setOpen(open === set.code ? null : set.code)}
                >
                  <strong>{setName(set.code, data, t)}</strong>
                </button>{" "}
                <span className="muted small">
                  {t("permission_sets.count", { count: set.permissions.length })}
                  {set.archived ? ` · ${t("permission_sets.archived")}` : ""}
                  {set.based_on ? ` · ${t("permission_sets.based_on_short", { set: setName(set.based_on, data, t) })}` : ""}
                </span>
                {open === set.code ? <SetDetail set={set} catalog={data} /> : null}
              </div>
            ))}
          </Card>
        );
      })}
    </div>
  );
}

function SetDetail({ set, catalog }: { set: PermissionSetEntry; catalog: PermissionCatalog }) {
  const can = useCan();
  const [editing, setEditing] = useState(false);
  const manage = can("employee.sets.manage") && !set.is_system;
  return (
    <div className="stack" style={{ marginTop: 8 }}>
      {editing ? (
        <SetForm catalog={catalog} set={set} onDone={() => setEditing(false)} />
      ) : (
        <>
          <PermissionList codes={set.permissions} catalog={catalog} />
          {manage ? <SetActions set={set} onEdit={() => setEditing(true)} /> : null}
        </>
      )}
      {can("employee.rights.manage") ? <Holders set={set} catalog={catalog} /> : null}
    </div>
  );
}

function PermissionList({ codes, catalog }: { codes: string[]; catalog: PermissionCatalog }) {
  const { t } = useTranslation();
  return (
    <div className="small">
      {bySection(catalog)
        .map(([section, list]) => [section, list.filter((c) => codes.includes(c))] as const)
        .filter(([, list]) => list.length)
        .map(([section, list]) => (
          <div key={section} style={{ marginBottom: 4 }}>
            <strong>{t(`permission_sections.${section}`, { defaultValue: section })}:</strong>{" "}
            {list.map((c) => permissionName(c, t)).join(", ")}
          </div>
        ))}
      {codes.length === 0 ? <span className="muted">{t("permission_sets.empty")}</span> : null}
    </div>
  );
}

function useRefresh() {
  const company = useCompany();
  const client = useQueryClient();
  return async () => {
    await client.invalidateQueries({ queryKey: ["permission-catalog", company.public_id] });
    await client.invalidateQueries({ queryKey: ["set-holders", company.public_id] });
    await client.invalidateQueries({ queryKey: ["employees", company.public_id] });
  };
}

function SetActions({ set, onEdit }: { set: PermissionSetEntry; onEdit: () => void }) {
  const { t } = useTranslation();
  const company = useCompany();
  const refresh = useRefresh();
  const state = useCommand<{ expected_version: number }, unknown>(
    `/c/${company.public_id}/permission-sets/${set.id}/${set.archived ? "restore" : "archive"}`,
  );
  return (
    <>
      <div className="actions">
        {!set.archived ? (
          <button type="button" className="secondary" onClick={onEdit}>
            {t("permission_sets.edit")}
          </button>
        ) : null}
        <button
          type="button"
          className={set.archived ? undefined : "danger"}
          disabled={state.pending}
          onClick={async () => {
            if ((await state.run({ expected_version: set.version })) !== null) await refresh();
          }}
        >
          {set.archived ? t("permission_sets.restore") : t("permission_sets.archive")}
        </button>
      </div>
      {!set.archived ? <p className="muted small">{t("permission_sets.archive_note")}</p> : null}
      <ErrorMessage error={state.error} />
    </>
  );
}

/** Creating (empty or based on another set) or changing an own set. The permissions are grouped
 * by section in collapsible blocks, so the form stays usable on a phone. */
function SetForm({
  catalog,
  set,
  onDone,
}: {
  catalog: PermissionCatalog;
  set?: PermissionSetEntry;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const company = useCompany();
  const refresh = useRefresh();
  const [name, setNameValue] = useState(set?.name ?? "");
  const [basedOn, setBasedOn] = useState("");
  const [codes, setCodes] = useState<string[]>(set?.permissions ?? []);
  const create = useCommand<Record<string, unknown>, unknown>(`/c/${company.public_id}/permission-sets`);
  const change = useCommand<Record<string, unknown>, unknown>(
    `/c/${company.public_id}/permission-sets/${set?.id ?? ""}`,
    "PATCH",
  );
  const command = set ? change : create;

  function chooseBase(value: string) {
    setBasedOn(value);
    const base = catalog.sets.find((s) => s.code === value);
    setCodes(base ? [...base.permissions] : []);
  }

  function toggle(code: string) {
    setCodes(codes.includes(code) ? codes.filter((c) => c !== code) : [...codes, code]);
  }

  function toggleSection(list: string[], on: boolean) {
    setCodes(on ? [...new Set([...codes, ...list])] : codes.filter((c) => !list.includes(c)));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const body: Record<string, unknown> = set
      ? { name, permissions: codes, expected_version: set.version }
      : { name, based_on: basedOn || null, permissions: codes };
    if ((await command.run(body)) !== null) {
      await refresh();
      onDone();
    }
  }

  return (
    <Card title={set ? t("permission_sets.edit") : t("permission_sets.create")}>
      <form className="stack" onSubmit={submit}>
        <Field label={t("permission_sets.name")}>
          <input value={name} maxLength={150} onChange={(e) => setNameValue(e.target.value)} required />
        </Field>
        {!set ? (
          <Field label={t("permission_sets.based_on")} hint={t("permission_sets.based_on_hint")}>
            <select value={basedOn} onChange={(e) => chooseBase(e.target.value)}>
              <option value="">{t("permission_sets.empty_set")}</option>
              {catalog.sets
                .filter((s) => !s.archived)
                .map((s) => (
                  <option key={s.code} value={s.code}>
                    {setName(s.code, catalog, t)}
                  </option>
                ))}
            </select>
          </Field>
        ) : (
          <p className="muted small">{t("permission_sets.change_note")}</p>
        )}
        <div className="small">{t("permission_sets.count", { count: codes.length })}</div>
        {bySection(catalog).map(([section, list]) => {
          const chosen = list.filter((c) => codes.includes(c)).length;
          return (
            <details key={section}>
              <summary>
                {t(`permission_sections.${section}`, { defaultValue: section })}{" "}
                <span className="muted small">
                  {chosen}/{list.length}
                </span>
              </summary>
              <div className="stack" style={{ padding: "4px 0 8px 8px" }}>
                <label className="small">
                  <input
                    type="checkbox"
                    checked={chosen === list.length}
                    onChange={(e) => toggleSection(list, e.target.checked)}
                  />{" "}
                  {t("permission_sets.all_of_section")}
                </label>
                {list.map((code) => (
                  <label key={code} className="small" style={{ display: "block", padding: "4px 0" }}>
                    <input type="checkbox" checked={codes.includes(code)} onChange={() => toggle(code)} />{" "}
                    {permissionName(code, t)}
                    {catalog.broad.includes(code) ? (
                      <span className="badge warn" style={{ marginLeft: 6 }}>
                        {t("permission_sets.broad")}
                      </span>
                    ) : null}
                  </label>
                ))}
              </div>
            </details>
          );
        })}
        <ErrorMessage error={command.error} />
        <div className="actions">
          <button type="submit" disabled={command.pending || !name.trim()}>
            {t("common.save")}
          </button>
          <button type="button" className="secondary" onClick={onDone}>
            {t("common.cancel")}
          </button>
        </div>
      </form>
    </Card>
  );
}

/** The holders of the set and what applying it would add and remove (§ 23.1.3). The actor's own
 * rights are never changed by himself (§ 23.3.2). */
function Holders({ set, catalog }: { set: PermissionSetEntry; catalog: PermissionCatalog }) {
  const { t } = useTranslation();
  const company = useCompany();
  const refresh = useRefresh();
  const holders = useApi<SetHolders>(
    ["set-holders", company.public_id, set.code],
    `/c/${company.public_id}/permission-sets/${encodeURIComponent(set.code)}/holders`,
  );
  const [chosen, setChosen] = useState<string[]>([]);
  const [result, setResult] = useState<"applied" | "pending" | null>(null);
  const apply = useCommand<Record<string, unknown>, { rights_requests: RightsRequest[] }>(
    `/c/${company.public_id}/permission-sets/${encodeURIComponent(set.code)}/apply`,
  );
  const items = holders.data?.holders ?? [];
  const outdated = items.filter((h) => (h.added.length || h.removed.length) && !h.own);

  async function run(extra: Record<string, string> = {}): Promise<boolean> {
    setResult(null);
    const done = await apply.run({ employment_ids: chosen, ...extra });
    if (done !== null) {
      setResult(done.rights_requests.length ? "pending" : "applied");
      setChosen([]);
      await refresh();
    }
    return done !== null;
  }

  return (
    <div className="stack">
      <strong className="small">{t("permission_sets.holders", { count: items.length })}</strong>
      {holders.isLoading ? <Loading /> : null}
      {items.map((h) => {
        const changes = h.added.length || h.removed.length;
        return (
          <div key={h.assignment_id} className="small" style={{ padding: "4px 0" }}>
            <label>
              {changes && !h.own ? (
                <input
                  type="checkbox"
                  checked={chosen.includes(h.employment_id)}
                  onChange={() =>
                    setChosen(
                      chosen.includes(h.employment_id)
                        ? chosen.filter((x) => x !== h.employment_id)
                        : [...chosen, h.employment_id],
                    )
                  }
                />
              ) : null}{" "}
              {h.name || h.public_id || "—"}
            </label>{" "}
            <span className="muted">
              {h.scope.locations === "all" ? t("rights.whole_company") : t("rights.some_locations")}
              {h.own ? ` · ${t("permission_sets.own_rights")}` : ""}
              {!changes ? ` · ${t("permission_sets.up_to_date")}` : ""}
            </span>
            {h.added.length ? (
              <div>
                + {h.added.map((c) => permissionName(c, t)).join(", ")}
                {h.broad ? <span className="badge warn" style={{ marginLeft: 6 }}>{t("permission_sets.broad")}</span> : null}
              </div>
            ) : null}
            {h.removed.length ? <div>− {h.removed.map((c) => permissionName(c, t)).join(", ")}</div> : null}
          </div>
        );
      })}
      {outdated.length ? (
        <>
          <div className="actions">
            <button
              type="button"
              className="secondary"
              onClick={() => setChosen(outdated.map((h) => h.employment_id))}
            >
              {t("permission_sets.choose_all")}
            </button>
          </div>
          {apply.error?.code === BROAD_CODE_REQUIRED ? (
            <CodeConfirm
              action={BROAD_ACTION}
              label={t("rights.broad_code_send")}
              onConfirm={(flow, code) => run({ flow, code })}
              error={null}
            />
          ) : (
            <>
              <ErrorMessage error={apply.error} />
              <div className="actions">
                <button type="button" disabled={!chosen.length || apply.pending} onClick={() => void run()}>
                  {t("permission_sets.apply_chosen", { count: chosen.length, set: setName(set.code, catalog, t) })}
                </button>
              </div>
            </>
          )}
        </>
      ) : null}
      {result === "applied" ? <div className="alert ok">{t("permission_sets.applied")}</div> : null}
      {result === "pending" ? <div className="alert warn">{t("rights.awaiting_confirmation")}</div> : null}
    </div>
  );
}
