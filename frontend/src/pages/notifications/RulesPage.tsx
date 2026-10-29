import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "../../api/queries";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { useCompany } from "../../layout/CompanyShell";

type Rule = {
  event: string;
  kind: string;
  mandatory: boolean;
  has_lead: boolean;
  can_task: boolean;
  enabled: boolean;
  lead_minutes: number;
  repeat_minutes: number;
  recipient_ids: string[];
  auto_task: boolean;
  version: number;
};
type Employee = { id: string; name: string };

/** Notifications and automatic tasks of the company (§ 19.1.9, § 20.3.4–20.3.6; З-7, З-8). */
export function NotificationRulesPage() {
  const { t } = useTranslation();
  const company = useCompany();
  const query = useApi<{ rules: Rule[]; employees: Employee[] }>(
    ["notification-rules", company.public_id],
    `/c/${company.public_id}/notification-rules`,
  );
  if (query.isLoading || !query.data) return <Loading />;
  const mandatory = query.data.rules.filter((r) => r.mandatory);
  const working = query.data.rules.filter((r) => !r.mandatory);
  return (
    <div className="page">
      <h1>{t("settings.notifications")}</h1>
      <p className="muted">{t("notifications.rules_hint")}</p>
      <Card title={t("notifications.working")}>
        {working.map((r) => (
          <RuleRow key={`${r.event}-${r.version}`} rule={r} employees={query.data.employees} />
        ))}
      </Card>
      <Card title={t("notifications.mandatory")}>
        <p className="muted small">{t("notifications.mandatory_hint")}</p>
        {mandatory.map((r) => (
          <div key={r.event} className="small">
            {t(`notifications.rule_${r.event.replaceAll(".", "_")}`)}
          </div>
        ))}
      </Card>
    </div>
  );
}

function RuleRow({ rule, employees }: { rule: Rule; employees: Employee[] }) {
  const { t } = useTranslation();
  const company = useCompany();
  const queryClient = useQueryClient();
  const [enabled, setEnabled] = useState(rule.enabled);
  const [lead, setLead] = useState(String(rule.lead_minutes / 60));
  const [repeat, setRepeat] = useState(String(rule.repeat_minutes / 60));
  const [people, setPeople] = useState<string[]>(rule.recipient_ids);
  const [task, setTask] = useState(rule.auto_task);
  const save = useCommand<Record<string, unknown>, unknown>(`/c/${company.public_id}/notification-rules/change`);

  async function submit() {
    const result = await save.run({
      event: rule.event,
      enabled,
      lead_minutes: Math.round(Number(lead || "0") * 60),
      repeat_minutes: Math.round(Number(repeat || "0") * 60),
      recipient_ids: people,
      auto_task: task,
      expected_version: rule.version,
    });
    if (result) await queryClient.invalidateQueries({ queryKey: ["notification-rules"] });
  }

  return (
    <div style={{ borderBottom: "1px solid var(--border, #ddd)", padding: "8px 0" }}>
      <label>
        <input type="checkbox" checked={enabled} onChange={() => setEnabled(!enabled)} />{" "}
        <strong>{t(`notifications.rule_${rule.event.replaceAll(".", "_")}`)}</strong>
      </label>
      {enabled ? (
        <div className="row">
          {rule.has_lead ? (
            <Field label={t("notifications.lead_hours")}>
              <input value={lead} inputMode="decimal" onChange={(e) => setLead(e.target.value.replace(/[^\d.]/g, ""))} />
            </Field>
          ) : null}
          <Field label={t("notifications.repeat_hours")} hint={t("notifications.repeat_hint")}>
            <input value={repeat} inputMode="decimal" onChange={(e) => setRepeat(e.target.value.replace(/[^\d.]/g, ""))} />
          </Field>
          <Field label={t("notifications.recipients")} hint={t("notifications.recipients_hint")}>
            <select
              multiple
              value={people}
              onChange={(e) => setPeople([...e.target.selectedOptions].map((o) => o.value))}
            >
              {employees.map((e) => (
                <option key={e.id} value={e.id}>
                  {e.name}
                </option>
              ))}
            </select>
          </Field>
          {rule.can_task ? (
            <label className="small">
              <input type="checkbox" checked={task} onChange={() => setTask(!task)} /> {t("notifications.auto_task")}
            </label>
          ) : null}
        </div>
      ) : null}
      <div className="actions">
        <button type="button" className="secondary" disabled={save.pending} onClick={() => void submit()}>
          {t("common.save")}
        </button>
      </div>
      <ErrorMessage error={save.error} />
    </div>
  );
}
