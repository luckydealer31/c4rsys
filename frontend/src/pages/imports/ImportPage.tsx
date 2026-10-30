import { useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router";

import { ApiError, uploadImport } from "../../api/client";
import { useApi } from "../../api/queries";
import type { ImportKind, ImportOperation, ImportRow } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { formatMoment } from "../../time";

const KINDS: ImportKind[] = ["items", "vehicles", "clients", "counterparties", "stock"];
/** The usual right to the section of the data, needed besides the right to import (§ 22.5.1). */
const SECTION: Record<ImportKind, string> = {
  items: "warehouse.catalog.manage",
  vehicles: "vehicle.create",
  clients: "client.create",
  counterparties: "counterparty.create",
  stock: "warehouse.receive",
};
const RECORD: Record<ImportKind, string> = {
  items: "/warehouse/items/",
  vehicles: "/vehicles/",
  clients: "/clients/",
  counterparties: "/clients/counterparties/",
  stock: "/warehouse/items/",
};

/** Initial stock links to its item: a row is a line of a receipt, a match is a lot. */
function recordPath(kind: ImportKind, row: ImportRow, id: string): string {
  return `${RECORD[kind]}${kind === "stock" ? row.values.item_id : id}`;
}

function rowName(kind: ImportKind, values: Record<string, string>, row?: ImportRow): string {
  if (kind === "stock") {
    const form = values.packs
      ? `${values.packs} × ${values.packaging_quantity}`
      : values.quantity || (values.lot_code || values.lot_id ? "" : "1");
    return [row?.extra?.item_name, values.lot_code || values.lot_id, form].filter(Boolean).join(" · ");
  }
  if (kind === "vehicles") {
    return values.name || [values.make, values.model, values.plate, values.vin].filter(Boolean).join(" ");
  }
  if (kind === "clients" || kind === "counterparties") {
    return values.company_name || [values.last_name, values.first_name].filter(Boolean).join(" ");
  }
  return values.name ?? "";
}

/** XLSX import (§ 22.2–22.5): templates of the catalog, vehicles, clients, counterparties and
 * initial stock (§ 22.4.2);
 * check with errors and matches, the choice for each row, posting after confirmation, a file
 * for correction and its upload by row codes, history and cancel of the whole import. */
export function ImportPage({ defaultKind = "items" }: { defaultKind?: ImportKind }) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const kind = (KINDS.find((k) => k === params.get("kind")) ?? defaultKind) as ImportKind;
  const input = useRef<HTMLInputElement>(null);
  const [current, setCurrent] = useState<ImportOperation | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [pending, setPending] = useState(false);
  const base = `/c/${company.public_id}/imports`;
  const allowed = can("import.run") && can(SECTION[kind]);
  const history = useApi<{ imports: ImportOperation[] }>(
    ["imports", company.public_id, kind],
    `${base}?kind=${kind}`,
    can("import.history.view"),
  );

  async function choose(file: File | undefined) {
    if (!file) return;
    setPending(true);
    setError(null);
    try {
      const result = await uploadImport<{ import: ImportOperation }>(base, file, { kind });
      setCurrent(result.import);
      await client.invalidateQueries({ queryKey: ["imports", company.public_id] });
    } catch (caught) {
      setError(caught instanceof ApiError ? caught : new ApiError(0, null));
    } finally {
      setPending(false);
      if (input.current) input.current.value = "";
    }
  }

  async function open(id: string) {
    const detail = await fetch(`/api${base}/${id}`, { credentials: "same-origin" });
    if (detail.ok) setCurrent(((await detail.json()) as { import: ImportOperation }).import);
  }

  return (
    <div className="page">
      <h1>{t("imports.title")}</h1>
      <div className="tabs">
        {KINDS.map((k) => (
          <button
            key={k}
            type="button"
            className={k === kind ? "" : "secondary"}
            onClick={() => {
              setCurrent(null);
              setParams({ kind: k });
            }}
          >
            {t(`imports.kind_${k}`)}
          </button>
        ))}
      </div>
      <Card>
        <p>{t(`imports.hint_${kind}`)}</p>
        {allowed ? (
          <div className="actions">
            <a className="button secondary" href={`/api${base}/template/${kind}`}>
              {t("warehouse.import_template")}
            </a>
            <input
              ref={input}
              type="file"
              accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              hidden
              onChange={(e) => void choose(e.target.files?.[0])}
            />
            <button type="button" disabled={pending} onClick={() => input.current?.click()}>
              {t("warehouse.import_choose")}
            </button>
          </div>
        ) : (
          <p className="muted">{t("imports.no_right")}</p>
        )}
        <ErrorMessage error={error} />
      </Card>
      {current ? <ImportResult operation={current} onChange={setCurrent} /> : null}
      {can("import.history.view") ? (
        <Card title={t("warehouse.import_history")}>
          {history.isLoading ? <Loading /> : null}
          <table>
            <tbody>
              {(history.data?.imports ?? []).map((operation) => (
                <tr key={operation.id}>
                  <td>{operation.file_name}</td>
                  <td>{formatMoment(operation.created_at, company.timezone ?? "UTC", i18n.language)}</td>
                  <td>{t(`warehouse.import_state_${operation.state}`)}</td>
                  <td>
                    {operation.state === "posted"
                      ? t("warehouse.import_counts", {
                          loaded: operation.loaded_count,
                          skipped: operation.skipped_count,
                        })
                      : ""}
                  </td>
                  <td>
                    <button type="button" className="secondary" onClick={() => void open(operation.id)}>
                      {t("warehouse.import_open")}
                    </button>{" "}
                    {operation.files_removed ? (
                      <span className="muted small">{t("imports.files_removed")}</span>
                    ) : (
                      <a href={`/api${base}/${operation.id}/file`}>{t("warehouse.import_source")}</a>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      ) : null}
    </div>
  );
}

function ImportResult({
  operation,
  onChange,
}: {
  operation: ImportOperation;
  onChange: (operation: ImportOperation) => void;
}) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const kind = operation.kind;
  const base = `/c/${company.public_id}/imports/${operation.id}`;
  const decide = useCommand<Record<string, unknown>, { import: ImportOperation }>(`${base}/decisions`);
  const post = useCommand<{ expected_version: number }, { import: ImportOperation }>(`${base}/post`);
  const cancel = useCommand<{ expected_version: number }, { import: ImportOperation }>(`${base}/cancel`);
  const corrected = useRef<HTMLInputElement>(null);
  const [uploadError, setUploadError] = useState<ApiError | null>(null);
  const rows = operation.rows ?? [];
  const checked = operation.state === "checked";
  const allowed = can("import.run") && can(SECTION[kind]);

  async function refresh(result: { import: ImportOperation } | null) {
    if (!result) return;
    onChange(result.import);
    await client.invalidateQueries({ queryKey: ["imports", company.public_id] });
  }

  async function choose(row: ImportRow, value: string) {
    const [action, target] = value.split(":");
    await refresh(
      await decide.run({
        decisions: [{ row: row.code, action, target: target ?? "" }],
        expected_version: operation.version,
      }),
    );
  }

  async function reupload(file: File | undefined) {
    if (!file) return;
    setUploadError(null);
    try {
      await refresh(
        await uploadImport<{ import: ImportOperation }>(`${base}/reupload`, file, {
          expected_version: String(operation.version),
        }),
      );
    } catch (caught) {
      setUploadError(caught instanceof ApiError ? caught : new ApiError(0, null));
    } finally {
      if (corrected.current) corrected.current.value = "";
    }
  }

  const blockers = (cancel.error?.params.blockers ?? []) as { reason: string; id: string; action?: string }[];

  return (
    <Card title={`${t("warehouse.import_check_title")}: ${operation.file_name}`}>
      <p>
        <span className={`badge ${operation.state === "posted" ? "ok" : "warn"}`}>
          {t(`warehouse.import_state_${operation.state}`)}
        </span>{" "}
        {operation.state === "posted"
          ? t("warehouse.import_counts", { loaded: operation.loaded_count, skipped: operation.skipped_count })
          : null}{" "}
        <span className="muted small">
          {t("imports.files_kept_until", {
            date: formatMoment(operation.files_kept_until, company.timezone ?? "UTC", i18n.language),
          })}
        </span>
      </p>
      {operation.warnings.map((w, index) => (
        <div key={index} className="alert warn">
          {t(`warehouse.import_warning_${w.code.toLowerCase()}`, w)}
        </div>
      ))}
      <table>
        <thead>
          <tr>
            <th>{t("warehouse.import_row")}</th>
            <th>{t("imports.record")}</th>
            <th>{t("warehouse.import_status")}</th>
            <th>{checked ? t("warehouse.import_action") : t("warehouse.import_result")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const posted = row.result === "created" || row.result === "linked";
            return (
              <tr key={row.code}>
                <td>
                  {row.number} <span className="muted small">{row.code}</span>
                </td>
                <td>
                  {rowName(kind, row.values, row) || "—"}
                  {row.group ? (
                    <div className="muted small">{t("imports.lot_group", { code: row.group })}</div>
                  ) : null}
                </td>
                <td>
                  {row.errors.length ? (
                    <span className="badge warn">
                      {row.errors.map((e) => `${e.field}: ${t(`errors.${e.code.toLowerCase()}`)}`).join("; ")}
                    </span>
                  ) : null}
                  {row.conflict ? <div className="badge warn">{t("imports.id_conflict")}</div> : null}
                  {row.matches.map((m) => (
                    <div key={m.id} className="small">
                      {t("warehouse.import_match")}:{" "}
                      <Link to={companyPath(company, recordPath(kind, row, m.id))}>{m.name}</Link> —{" "}
                      {t(`imports.by_${m.by}`)}
                    </div>
                  ))}
                  {row.extra?.packaging_missing && !posted ? (
                    <div className="badge warn">
                      {t("imports.packaging_missing", {
                        quantity: row.extra.packaging_missing.quantity,
                        unit: t(`warehouse.unit_short_${row.extra.packaging_missing.unit}`),
                        name: row.extra.packaging_missing.name || "—",
                      })}
                    </div>
                  ) : null}
                  {!row.errors.length && !row.matches.length && (!row.extra?.packaging_missing || posted) ? (
                    <span className="badge ok">{t("warehouse.import_ok")}</span>
                  ) : null}
                </td>
                <td>
                  {row.packaging_confirmed && !posted ? (
                    <span className="badge">{t("imports.packaging_confirmed")}</span>
                  ) : null}
                  {checked && !posted ? (
                    <select
                      value={row.action === "link" ? `link:${row.target ?? ""}` : row.action}
                      disabled={row.errors.length > 0 || decide.pending || !allowed}
                      onChange={(e) => void choose(row, e.target.value)}
                    >
                      {row.errors.length ? <option value="error">{t("warehouse.action_error")}</option> : null}
                      {!row.errors.length &&
                      !row.matches.length &&
                      !row.conflict &&
                      !row.extra?.packaging_missing ? (
                        <option value="create">{t("warehouse.action_create")}</option>
                      ) : null}
                      {!row.errors.length &&
                      row.extra?.packaging_missing &&
                      !row.packaging_confirmed &&
                      can("warehouse.catalog.manage") ? (
                        <option value="create_packaging">{t("imports.action_create_packaging")}</option>
                      ) : null}
                      <option value="skip">{t("warehouse.action_skip")}</option>
                      {kind !== "stock"
                        ? row.matches.map((m) => (
                            <option key={m.id} value={`link:${m.id}`}>
                              {t("warehouse.action_link")}: {m.name}
                            </option>
                          ))
                        : null}
                      {!row.errors.length && row.matches.length && !row.conflict ? (
                        <option value="create_duplicate">{t("warehouse.action_create_duplicate")}</option>
                      ) : null}
                    </select>
                  ) : row.result ? (
                    <>
                      {t(`warehouse.result_${row.result}`)}{" "}
                      {row.record_id ? (
                        <Link to={companyPath(company, recordPath(kind, row, row.record_id))}>→</Link>
                      ) : null}
                    </>
                  ) : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <ErrorMessage error={decide.error ?? post.error ?? cancel.error ?? uploadError} />
      {blockers.length ? (
        <ul className="small">
          {blockers.map((b, index) => (
            <li key={index}>
              {t(`imports.blocker_${b.reason}`)}: {b.action ? `${b.action} — ` : ""}
              {b.id}
            </li>
          ))}
        </ul>
      ) : null}
      {checked && allowed ? (
        <>
          <p className="muted small">{t("warehouse.import_post_hint")}</p>
          <div className="actions">
            <button
              type="button"
              disabled={post.pending}
              onClick={async () => refresh(await post.run({ expected_version: operation.version }))}
            >
              {t("warehouse.import_post")}
            </button>
          </div>
        </>
      ) : null}
      {operation.state === "posted" ? (
        <div className="actions">
          {allowed && operation.skipped_count > 0 ? (
            <>
              <a className="button secondary" href={`/api${base}/correction`}>
                {t("imports.correction")}
              </a>
              <input
                ref={corrected}
                type="file"
                accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                hidden
                onChange={(e) => void reupload(e.target.files?.[0])}
              />
              <button type="button" className="secondary" onClick={() => corrected.current?.click()}>
                {t("imports.reupload")}
              </button>
            </>
          ) : null}
          {can("import.cancel") && can(SECTION[kind]) ? (
            <button
              type="button"
              className="secondary"
              disabled={cancel.pending}
              onClick={async () => refresh(await cancel.run({ expected_version: operation.version }))}
            >
              {t("warehouse.import_cancel")}
            </button>
          ) : null}
        </div>
      ) : null}
      {operation.state === "posted" && operation.skipped_count > 0 ? (
        <p className="muted small">{t("imports.correction_hint")}</p>
      ) : null}
    </Card>
  );
}
