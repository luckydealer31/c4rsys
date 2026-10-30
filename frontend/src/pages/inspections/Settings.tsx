/** Settings of inspections: the templates of cars and motorcycles with their photo positions —
 * enabled, required, count and own positions; a save makes a new version (§ 10.3.5) — and the
 * policy of required readings and checks by kind and application (§ 10.3.1). */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import type { ApiError } from "../../api/client";
import type { InspectionPolicy, InspectionTemplate, TemplatePosition } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { formatMoment } from "../../time";
import { usePolicies, usePositionName, useRefreshInspections, useTemplates } from "./data";

export function InspectionTemplatesPage() {
  const { t } = useTranslation();
  const company = useCompany();
  const templates = useTemplates();
  return (
    <div className="page">
      <p>
        <Link to={companyPath(company, "/settings")}>← {t("nav.settings")}</Link>
      </p>
      <h1>{t("settings.inspection_templates")}</h1>
      <p className="muted">{t("inspections.templates_hint")}</p>
      {templates.isLoading ? <Loading /> : null}
      <ErrorMessage error={templates.error as ApiError} />
      {(templates.data?.templates ?? []).map((tpl) => (
        <TemplateEditor key={`${tpl.id}-${tpl.version}`} template={tpl} />
      ))}
    </div>
  );
}

function TemplateEditor({ template }: { template: InspectionTemplate }) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const refresh = useRefreshInspections();
  const positionName = usePositionName();
  const editable = can("inspection.policy.manage");
  const [positions, setPositions] = useState<TemplatePosition[]>(template.positions);
  const [newName, setNewName] = useState("");
  const save = useCommand<Record<string, unknown>, { template: InspectionTemplate }>(
    `/c/${company.public_id}/inspection-templates/${template.id}`,
    "PUT",
  );
  const changed = JSON.stringify(positions) !== JSON.stringify(template.positions);
  const base = new Set(template.base_positions);
  const update = (index: number, patch: Partial<TemplatePosition>) =>
    setPositions(positions.map((p, i) => (i === index ? { ...p, ...patch } : p)));

  function add() {
    const name = newName.trim();
    if (!name) return;
    let number = positions.length + 1;
    while (positions.some((p) => p.code === `own_${number}`)) number += 1;
    setPositions([...positions, { code: `own_${number}`, name, enabled: true, required: false, count: 1 }]);
    setNewName("");
  }

  async function submit() {
    const result = await save.run({ expected_version: template.version, positions });
    if (result) await refresh();
  }

  return (
    <Card title={t(`inspections.transport_${template.transport}`)}>
      <p className="muted small">
        {t("inspections.template_version", {
          number: template.version,
          when: formatMoment(template.updated_at, company.timezone ?? "UTC", i18n.language),
        })}
      </p>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>{t("inspections.position")}</th>
              <th>{t("inspections.position_enabled")}</th>
              <th>{t("inspections.position_required_column")}</th>
              <th>{t("inspections.position_count")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {positions.map((p, index) => (
              <tr key={p.code}>
                <td>
                  {base.has(p.code) ? (
                    positionName(p)
                  ) : (
                    <input
                      value={p.name ?? ""}
                      maxLength={100}
                      disabled={!editable}
                      onChange={(e) => update(index, { name: e.target.value })}
                    />
                  )}
                </td>
                <td>
                  <input
                    type="checkbox"
                    disabled={!editable}
                    checked={p.enabled}
                    onChange={() => update(index, { enabled: !p.enabled, required: p.enabled ? false : p.required })}
                  />
                </td>
                <td>
                  <input
                    type="checkbox"
                    disabled={!editable || !p.enabled}
                    checked={p.required}
                    onChange={() => update(index, { required: !p.required })}
                  />
                </td>
                <td>
                  <input
                    type="number"
                    min={1}
                    max={20}
                    style={{ width: 72 }}
                    disabled={!editable}
                    value={p.count}
                    onChange={(e) => update(index, { count: Math.max(1, Math.min(20, Number(e.target.value) || 1)) })}
                  />
                </td>
                <td>
                  {!base.has(p.code) && editable ? (
                    <button type="button" className="secondary" onClick={() => setPositions(positions.filter((_, i) => i !== index))}>
                      {t("inspections.remove_position")}
                    </button>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {editable ? (
        <>
          <div className="row" style={{ marginTop: 12 }}>
            <input
              value={newName}
              maxLength={100}
              placeholder={t("inspections.own_position_name")}
              onChange={(e) => setNewName(e.target.value)}
            />
            <div style={{ flex: "0 0 auto" }}>
              <button type="button" className="secondary" disabled={!newName.trim()} onClick={add}>
                {t("inspections.add_position")}
              </button>
            </div>
          </div>
          <ErrorMessage error={save.error} />
          <div className="actions">
            <button type="button" disabled={!changed || save.pending} onClick={() => void submit()}>
              {t("inspections.save_template")}
            </button>
            {changed ? (
              <button type="button" className="secondary" onClick={() => setPositions(template.positions)}>
                {t("common.cancel")}
              </button>
            ) : null}
          </div>
          <p className="muted small">{t("inspections.template_save_hint")}</p>
        </>
      ) : null}
    </Card>
  );
}

export function InspectionPolicyPage() {
  const { t } = useTranslation();
  const company = useCompany();
  const policies = usePolicies();
  return (
    <div className="page">
      <p>
        <Link to={companyPath(company, "/settings")}>← {t("nav.settings")}</Link>
      </p>
      <h1>{t("settings.inspection_policy")}</h1>
      <p className="muted">{t("inspections.policy_hint")}</p>
      {policies.isLoading ? <Loading /> : null}
      <ErrorMessage error={policies.error as ApiError} />
      <Card>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>{t("inspections.kind")}</th>
                <th>{t("inspections.policy_check")}</th>
                <th>{t("inspections.policy_odometer")}</th>
                <th>{t("inspections.policy_fuel")}</th>
                <th>{t("inspections.policy_cleanliness")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {(policies.data?.policies ?? []).map((p) => (
                <PolicyRow key={`${p.kind}-${p.application}-${p.version}`} policy={p} />
              ))}
            </tbody>
          </table>
        </div>
        <p className="muted small">
          {t("inspections.policy_rental_hint")}{" "}
          <Link to={companyPath(company, "/settings/rental")}>{t("settings.rental")}</Link>
        </p>
      </Card>
    </div>
  );
}

function PolicyRow({ policy }: { policy: InspectionPolicy }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const refresh = useRefreshInspections();
  const editable = can("inspection.policy.manage");
  const rentalCard = policy.check_required === null;
  const [value, setValue] = useState(policy);
  const save = useCommand<Record<string, unknown>, unknown>(`/c/${company.public_id}/inspection-policies`, "PUT");
  const changed = JSON.stringify(value) !== JSON.stringify(policy);
  const flag = (key: "check_required" | "odometer_required" | "fuel_required" | "cleanliness_required", locked: boolean) => (
    <input
      type="checkbox"
      disabled={!editable || locked}
      checked={Boolean(value[key])}
      onChange={() => setValue({ ...value, [key]: !value[key] })}
    />
  );
  async function submit() {
    const result = await save.run({
      kind: value.kind,
      application: value.application,
      check_required: rentalCard ? null : value.check_required,
      odometer_required: value.odometer_required,
      fuel_required: value.fuel_required,
      cleanliness_required: value.cleanliness_required,
      expected_version: policy.version,
    });
    if (result) await refresh();
  }
  return (
    <tr>
      <td>
        {t(`inspections.kind_${policy.kind}`)}
        <div className="muted small">{t(`inspections.application_${policy.application}`)}</div>
        {save.error ? <ErrorMessage error={save.error} /> : null}
      </td>
      <td>{rentalCard ? <span className="muted small">{t("inspections.policy_in_issue")}</span> : flag("check_required", false)}</td>
      <td>{flag("odometer_required", rentalCard)}</td>
      <td>{flag("fuel_required", rentalCard)}</td>
      <td>{flag("cleanliness_required", false)}</td>
      <td>
        {editable ? (
          <button type="button" disabled={!changed || save.pending} onClick={() => void submit()}>
            {t("common.save")}
          </button>
        ) : null}
      </td>
    </tr>
  );
}
