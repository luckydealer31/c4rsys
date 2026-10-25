import { useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { ApiError, uploadImport } from "../../api/client";
import { useApi } from "../../api/queries";
import type { ImportOperation, ImportRow } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { formatMoment } from "../../time";

/** XLSX import of the catalog (§ 22.2, § 22.4.1): template, check with errors and matches, the
 * choice for each row, posting after confirmation, history and cancel of the whole import. */
export function ImportPage() {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [current, setCurrent] = useState<ImportOperation | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [pending, setPending] = useState(false);
  const base = `/c/${company.public_id}/warehouse/imports`;
  const history = useApi<{ imports: ImportOperation[] }>(
    ["warehouse-imports", company.public_id],
    base,
    can("import.history.view"),
  );

  async function choose(file: File | undefined) {
    if (!file) return;
    setPending(true);
    setError(null);
    try {
      const result = await uploadImport<{ import: ImportOperation }>(base, file);
      setCurrent(result.import);
      await client.invalidateQueries({ queryKey: ["warehouse-imports", company.public_id] });
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
      <p>
        <Link to={companyPath(company, "/warehouse")}>← {t("nav.warehouse")}</Link>
      </p>
      <h1>{t("warehouse.import_title")}</h1>
      <Card>
        <p>{t("warehouse.import_template_hint")}</p>
        <div className="actions">
          <a className="button secondary" href={`/api${base}/template`}>
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
                    <a href={`/api${base}/${operation.id}/file`}>{t("warehouse.import_source")}</a>
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
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const base = `/c/${company.public_id}/warehouse/imports/${operation.id}`;
  const decide = useCommand<Record<string, unknown>, { import: ImportOperation }>(`${base}/decisions`);
  const post = useCommand<{ expected_version: number }, { import: ImportOperation }>(`${base}/post`);
  const cancel = useCommand<{ expected_version: number }, { import: ImportOperation }>(`${base}/cancel`);
  const rows = operation.rows ?? [];
  const checked = operation.state === "checked";

  async function refresh(result: { import: ImportOperation } | null) {
    if (!result) return;
    onChange(result.import);
    await client.invalidateQueries({ queryKey: ["warehouse-imports", company.public_id] });
    await client.invalidateQueries({ queryKey: ["warehouse-items", company.public_id] });
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

  return (
    <Card title={`${t("warehouse.import_check_title")}: ${operation.file_name}`}>
      <p>
        <span className={`badge ${operation.state === "posted" ? "ok" : "warn"}`}>
          {t(`warehouse.import_state_${operation.state}`)}
        </span>{" "}
        {operation.state === "posted"
          ? t("warehouse.import_counts", { loaded: operation.loaded_count, skipped: operation.skipped_count })
          : null}
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
            <th>{t("warehouse.name")}</th>
            <th>{t("warehouse.import_status")}</th>
            <th>{checked ? t("warehouse.import_action") : t("warehouse.import_result")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.code}>
              <td>
                {row.number} <span className="muted small">{row.code}</span>
              </td>
              <td>
                {row.values.name || "—"} <span className="muted small">{row.values.unit}</span>
              </td>
              <td>
                {row.errors.length ? (
                  <span className="badge warn">
                    {row.errors.map((e) => `${e.field}: ${t(`errors.${e.code.toLowerCase()}`)}`).join("; ")}
                  </span>
                ) : row.matches.length ? (
                  row.matches.map((m) => (
                    <div key={m.id} className="small">
                      {t("warehouse.import_match")}:{" "}
                      <Link to={companyPath(company, `/warehouse/items/${m.id}`)}>{m.name}</Link> —{" "}
                      {t(`warehouse.import_by_${m.by}`)}
                    </div>
                  ))
                ) : (
                  <span className="badge ok">{t("warehouse.import_ok")}</span>
                )}
              </td>
              <td>
                {checked ? (
                  <select
                    value={row.action === "link" ? `link:${row.target ?? ""}` : row.action}
                    disabled={row.errors.length > 0 || decide.pending || !can("import.run")}
                    onChange={(e) => void choose(row, e.target.value)}
                  >
                    {row.errors.length ? <option value="error">{t("warehouse.action_error")}</option> : null}
                    {!row.errors.length && !row.matches.length ? (
                      <option value="create">{t("warehouse.action_create")}</option>
                    ) : null}
                    <option value="skip">{t("warehouse.action_skip")}</option>
                    {row.matches.map((m) => (
                      <option key={m.id} value={`link:${m.id}`}>
                        {t("warehouse.action_link")}: {m.name}
                      </option>
                    ))}
                    {!row.errors.length && row.matches.length ? (
                      <option value="create_duplicate">{t("warehouse.action_create_duplicate")}</option>
                    ) : null}
                  </select>
                ) : row.result ? (
                  <>
                    {t(`warehouse.result_${row.result}`)}{" "}
                    {row.item_id ? (
                      <Link to={companyPath(company, `/warehouse/items/${row.item_id}`)}>→</Link>
                    ) : null}
                  </>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <ErrorMessage error={decide.error ?? post.error ?? cancel.error} />
      {checked && can("import.run") ? (
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
      {operation.state === "posted" && can("import.cancel") ? (
        <div className="actions">
          <button
            type="button"
            className="secondary"
            disabled={cancel.pending}
            onClick={async () => refresh(await cancel.run({ expected_version: operation.version }))}
          >
            {t("warehouse.import_cancel")}
          </button>
        </div>
      ) : null}
    </Card>
  );
}
