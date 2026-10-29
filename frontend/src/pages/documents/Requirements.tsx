import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "../../api/queries";
import type { DocumentTemplate } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { useCan, useCompany } from "../../layout/CompanyShell";
import { useDocumentKinds, useKindName } from "./DocumentsPage";

type Requirement = {
  id: string;
  stage: "before_issue" | "at_issue" | "at_return";
  need: "present" | "signed";
  template_id: string | null;
  template_title: string | null;
  kind_id: string | null;
};

const STAGES = ["before_issue", "at_issue", "at_return"] as const;

/** Documents required at the stages of rentals (§ 18.4.1; decision Р-8). */
export function DocumentRequirementsCard() {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const queryClient = useQueryClient();
  const kinds = useDocumentKinds();
  const kindName = useKindName();
  const base = `/c/${company.public_id}/document-requirements`;
  const list = useApi<{ requirements: Requirement[] }>(["document-requirements", company.public_id], base);
  const templates = useApi<{ templates: DocumentTemplate[] }>(
    ["document-templates", company.public_id],
    `/c/${company.public_id}/document-templates`,
    can("documents.view"),
  );
  const add = useCommand<Record<string, unknown>, unknown>(base);
  const remove = useCommand<{ id: string }, unknown>((body) => `${base}/${body.id}/remove`);
  const [stage, setStage] = useState<Requirement["stage"]>("before_issue");
  const [need, setNeed] = useState<Requirement["need"]>("present");
  const [what, setWhat] = useState("");
  const editable = can("company.settings");
  const lang = i18n.language.slice(0, 2);
  const choices = [
    ...(templates.data?.templates ?? [])
      .filter((x) => x.state === "active" && (!x.system || x.language === lang))
      .map((x) => ({ value: `template:${x.id}`, label: `${t("documents.template")}: ${x.title}` })),
    ...(kinds.data?.kinds ?? []).map((k) => ({ value: `kind:${k.id}`, label: `${t("documents.kind")}: ${kindName(k)}` })),
  ];
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["document-requirements"] });

  async function submit() {
    const [type, id] = what.split(":");
    const result = await add.run({ stage, need, template_id: type === "template" ? id : null, kind_id: type === "kind" ? id : null });
    if (result) {
      setWhat("");
      await refresh();
    }
  }

  if (list.isLoading) return <Loading />;
  const items = list.data?.requirements ?? [];
  return (
    <Card title={t("documents.requirements_title")}>
      <p className="muted small">{t("documents.requirements_hint")}</p>
      {STAGES.map((s) => {
        const rows = items.filter((r) => r.stage === s);
        return (
          <div key={s} className="small" style={{ marginBottom: 8 }}>
            <strong>{t(`documents.stage_${s}`)}</strong>
            {rows.length === 0 ? <div className="muted">{t("documents.requirements_none")}</div> : null}
            {rows.map((r) => (
              <div key={r.id}>
                {r.template_title ?? kindName(kinds.data?.kinds.find((k) => k.id === r.kind_id))} · {t(`documents.need_${r.need}`)}{" "}
                {editable ? (
                  <button type="button" className="link" disabled={remove.pending} onClick={() => void remove.run({ id: r.id }).then((x) => (x ? refresh() : undefined))}>
                    {t("documents.requirement_remove")}
                  </button>
                ) : null}
              </div>
            ))}
          </div>
        );
      })}
      {editable ? (
        <div className="row">
          <Field label={t("documents.stage")}>
            <select value={stage} onChange={(e) => setStage(e.target.value as Requirement["stage"])}>
              {STAGES.map((s) => (
                <option key={s} value={s}>
                  {t(`documents.stage_${s}`)}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("documents.requirement_what")}>
            <select value={what} onChange={(e) => setWhat(e.target.value)}>
              <option value="">—</option>
              {choices.map((c) => (
                <option key={c.value} value={c.value}>
                  {c.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("documents.need")}>
            <select value={need} onChange={(e) => setNeed(e.target.value as Requirement["need"])}>
              <option value="present">{t("documents.need_present")}</option>
              <option value="signed">{t("documents.need_signed")}</option>
            </select>
          </Field>
          <div className="actions">
            <button type="button" disabled={add.pending || !what} onClick={() => void submit()}>
              {t("documents.requirement_add")}
            </button>
          </div>
        </div>
      ) : null}
      <ErrorMessage error={add.error ?? remove.error} />
    </Card>
  );
}

type ActSetting = { act_required: boolean; act_signed: boolean; version: number };

/** The act of completed works of a client's service (§ 16.7.6; decision Р-9). */
export function ServiceActCard() {
  const company = useCompany();
  const path = `/c/${company.public_id}/service-act-setting`;
  const query = useApi<{ setting: ActSetting }>(["service-act-setting", company.public_id], path);
  if (!query.data) return <Loading />;
  return <ServiceActFields key={query.data.setting.version} setting={query.data.setting} path={path} />;
}

function ServiceActFields({ setting, path }: { setting: ActSetting; path: string }) {
  const { t } = useTranslation();
  const can = useCan();
  const queryClient = useQueryClient();
  const [required, setRequired] = useState(setting.act_required);
  const [signed, setSigned] = useState(setting.act_signed);
  const save = useCommand<Record<string, unknown>, { setting: ActSetting }>(path, "PUT");
  const editable = can("company.settings");

  async function submit() {
    const result = await save.run({ act_required: required, act_signed: required && signed, expected_version: setting.version });
    if (result) await queryClient.invalidateQueries({ queryKey: ["service-act-setting"] });
  }

  return (
    <Card title={t("documents.service_act_title")}>
      <p className="muted small">{t("documents.service_act_hint")}</p>
      <label className="small" style={{ display: "block" }}>
        <input type="checkbox" checked={required} disabled={!editable} onChange={() => setRequired(!required)} /> {t("documents.service_act_required")}
      </label>
      <label className="small" style={{ display: "block" }}>
        <input type="checkbox" checked={required && signed} disabled={!editable || !required} onChange={() => setSigned(!signed)} />{" "}
        {t("documents.service_act_signed")}
      </label>
      {editable ? (
        <div className="actions">
          <button type="button" disabled={save.pending} onClick={() => void submit()}>
            {t("common.save")}
          </button>
        </div>
      ) : null}
      <ErrorMessage error={save.error} />
    </Card>
  );
}
