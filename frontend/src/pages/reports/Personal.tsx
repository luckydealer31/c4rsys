import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "../../api/queries";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { useCompany } from "../../layout/CompanyShell";
import { metricLabel, ReportView } from "./Report";

type Dashboard = { id: string; name: string; metrics: string[]; version: number };
type Metric = { id: string; dashboard: string; code: string };

/** Personal dashboards: the employee's own choice of system metrics (§ 21.2.4–21.2.5; О-8). */
export function PersonalDashboards() {
  const { t } = useTranslation();
  const company = useCompany();
  const queryClient = useQueryClient();
  const base = `/c/${company.public_id}/reports/personal`;
  const list = useApi<{ dashboards: Dashboard[]; catalog: Metric[] }>(["personal-dashboards", company.public_id], base);
  const create = useCommand<Record<string, unknown>, { dashboard: Dashboard }>(base);
  const [chosen, setChosen] = useState("");
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState("");
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["personal-dashboards"] });
  if (list.isLoading || !list.data) return <Loading />;
  const dashboards = list.data.dashboards;
  const current = dashboards.find((d) => d.id === chosen) ?? dashboards[0];

  async function add() {
    const result = await create.run({ name, metrics: [] });
    if (result) {
      setName("");
      setChosen(result.dashboard.id);
      setEditing(true);
      await refresh();
    }
  }

  return (
    <div>
      <div className="row no-print">
        {dashboards.length ? (
          <Field label={t("reports.personal_choose")}>
            <select value={current?.id ?? ""} onChange={(e) => setChosen(e.target.value)}>
              {dashboards.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
          </Field>
        ) : null}
        <Field label={t("reports.personal_new")}>
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <div className="actions">
          <button type="button" className="secondary" disabled={!name.trim() || create.pending} onClick={() => void add()}>
            {t("reports.personal_create")}
          </button>
          {current ? (
            <button type="button" className="secondary" onClick={() => setEditing(!editing)}>
              {editing ? t("reports.personal_done") : t("reports.personal_edit")}
            </button>
          ) : null}
        </div>
      </div>
      <ErrorMessage error={create.error} />
      {!dashboards.length ? <p className="muted">{t("reports.personal_none")}</p> : null}
      {current && editing ? (
        <Editor key={`${current.id}-${current.version}`} dashboard={current} catalog={list.data.catalog} onDone={refresh} />
      ) : null}
      {current && current.metrics.length ? <ReportView code={`p-${current.id}`} title={current.name} /> : null}
      {current && !current.metrics.length && !editing ? <p className="muted">{t("reports.personal_empty")}</p> : null}
    </div>
  );
}

function Editor({ dashboard, catalog, onDone }: { dashboard: Dashboard; catalog: Metric[]; onDone: () => Promise<unknown> }) {
  const { t } = useTranslation();
  const company = useCompany();
  const path = `/c/${company.public_id}/reports/personal/${dashboard.id}`;
  const save = useCommand<Record<string, unknown>, { dashboard: Dashboard }>(path, "PUT");
  const remove = useCommand<Record<string, unknown>, unknown>(path, "DELETE");
  const [name, setName] = useState(dashboard.name);
  const [metrics, setMetrics] = useState<string[]>(dashboard.metrics);
  const groups = [...new Set(catalog.map((m) => m.dashboard))];
  const toggle = (id: string) => setMetrics(metrics.includes(id) ? metrics.filter((x) => x !== id) : [...metrics, id]);
  return (
    <Card title={t("reports.personal_edit")}>
      <Field label={t("reports.personal_name")}>
        <input value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <p className="muted small">{t("reports.personal_hint")}</p>
      {groups.map((g) => (
        <div key={g} className="small" style={{ marginBottom: 8 }}>
          <strong>{t(`reports.dashboard_${g}`)}</strong>
          <div>
            {catalog
              .filter((m) => m.dashboard === g)
              .map((m) => (
                <label key={m.id} style={{ display: "inline-block", marginRight: 12 }}>
                  <input type="checkbox" checked={metrics.includes(m.id)} onChange={() => toggle(m.id)} /> {metricLabel(t, m.code)}
                </label>
              ))}
          </div>
        </div>
      ))}
      <div className="actions">
        <button
          type="button"
          disabled={save.pending || !name.trim()}
          onClick={() => void save.run({ name, metrics, expected_version: dashboard.version }).then((r) => (r ? onDone() : undefined))}
        >
          {t("common.save")}
        </button>
        <button type="button" className="danger" disabled={remove.pending} onClick={() => void remove.run({}).then((r) => (r ? onDone() : undefined))}>
          {t("reports.personal_remove")}
        </button>
      </div>
      <ErrorMessage error={save.error ?? remove.error} />
    </Card>
  );
}
