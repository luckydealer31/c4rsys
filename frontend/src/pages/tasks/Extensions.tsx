import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { useApi } from "../../api/queries";
import type { WorkTask } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";

export type Group = { id: string; name: string; description: string; version: number };
export type Tag = { id: string; name: string };
export const WEEKDAYS = [1, 2, 3, 4, 5, 6, 7] as const;

export function useGroups() {
  const company = useCompany();
  return useApi<{ groups: Group[] }>(["task-groups", company.public_id], `/c/${company.public_id}/tasks/groups`);
}

export function useTags() {
  const company = useCompany();
  return useApi<{ tags: Tag[] }>(["task-tags", company.public_id], `/c/${company.public_id}/tasks/tags`);
}

function useTaskAction(task: WorkTask) {
  const company = useCompany();
  const queryClient = useQueryClient();
  const base = `/c/${company.public_id}/tasks/${task.id}`;
  const act = useCommand<Record<string, unknown> & { action: string }, unknown>((body) => `${base}/${body.action}`);
  const run = (action: string, body: Record<string, unknown> = {}) =>
    act.run({ action, expected_version: task.version, ...body }).then(async (r) => {
      if (r) {
        await queryClient.invalidateQueries({ queryKey: ["task"] });
        await queryClient.invalidateQueries({ queryKey: ["tasks"] });
      }
      return r;
    });
  return { run, pending: act.pending, error: act.error };
}

/** Checklist: the executor marks items; the composition is changed with «manage» (§ 19.4.4). */
export function ChecklistCard({ task }: { task: WorkTask }) {
  const { t } = useTranslation();
  const { run, pending, error } = useTaskAction(task);
  const [text, setText] = useState("");
  const active = ["open", "in_progress", "waiting", "paused"].includes(task.state);
  const canMark = active && (task.is_mine || task.can.manage);
  if (!task.checklist.length && !(active && task.can.manage)) return null;
  return (
    <Card title={t("tasks.checklist")}>
      {task.checklist_required ? <p className="muted small">{t("tasks.checklist_required")}</p> : null}
      {task.checklist.map((item, index) => (
        <div key={item.id} className="small">
          <label>
            <input
              type="checkbox"
              checked={item.done}
              disabled={!canMark || pending}
              onChange={() => void run("checklist_mark", { item_id: item.id, done: !item.done })}
            />{" "}
            {item.text}
          </label>
          {active && task.can.manage ? (
            <>
              {" "}
              {index > 0 ? (
                <button type="button" className="link" disabled={pending} onClick={() => void run("checklist_move", { item_id: item.id, position: index - 1 })}>
                  ↑
                </button>
              ) : null}{" "}
              <button type="button" className="link" disabled={pending} onClick={() => void run("checklist_remove", { item_id: item.id })}>
                {t("tasks.remove")}
              </button>
            </>
          ) : null}
        </div>
      ))}
      {active && task.can.manage ? (
        <div className="row">
          <Field label={t("tasks.checklist_item")}>
            <input value={text} onChange={(e) => setText(e.target.value)} />
          </Field>
          <div className="actions">
            <button type="button" className="secondary" disabled={pending || !text.trim()} onClick={() => void run("checklist_add", { text }).then(() => setText(""))}>
              {t("tasks.add")}
            </button>
          </div>
        </div>
      ) : null}
      <ErrorMessage error={error} />
    </Card>
  );
}

/** «Start after completion» (§ 19.4.6). */
export function DependenciesCard({ task }: { task: WorkTask }) {
  const { t } = useTranslation();
  const company = useCompany();
  const { run, pending, error } = useTaskAction(task);
  const active = ["open", "in_progress", "waiting", "paused"].includes(task.state);
  const others = useApi<{ tasks: WorkTask[] }>(["tasks", company.public_id, "all-active"], `/c/${company.public_id}/tasks`, active && task.can.manage);
  const taken = new Set([task.id, ...task.predecessors.map((p) => p.id)]);
  if (!task.predecessors.length && !(active && task.can.manage)) return null;
  return (
    <Card title={t("tasks.dependencies")}>
      {task.blocked ? <p className="small"><span className="badge warn">{t("tasks.blocked")}</span></p> : null}
      {task.predecessors.map((p) => (
        <div key={p.id} className="small">
          <Link to={companyPath(company, `/tasks/${p.id}`)}>№ {p.number}</Link> {p.title} · {t(`tasks.state_${p.state}`)}{" "}
          {active && task.can.manage ? (
            <button type="button" className="link" disabled={pending} onClick={() => void run("undepend", { predecessor_id: p.id })}>
              {t("tasks.remove")}
            </button>
          ) : null}
        </div>
      ))}
      {active && task.can.manage ? (
        <Field label={t("tasks.start_after")}>
          <select value="" onChange={(e) => (e.target.value ? void run("depend", { predecessor_id: e.target.value }) : undefined)}>
            <option value="">—</option>
            {(others.data?.tasks ?? [])
              .filter((x) => !taken.has(x.id))
              .map((x) => (
                <option key={x.id} value={x.id}>
                  № {x.number} {x.title}
                </option>
              ))}
          </select>
        </Field>
      ) : null}
      <ErrorMessage error={error} />
    </Card>
  );
}

/** Group and tags of a task: chosen from the company's lists (§ 23.9.5). */
export function GroupTagsCard({ task }: { task: WorkTask }) {
  const { t } = useTranslation();
  const groups = useGroups();
  const tags = useTags();
  const { run, pending, error } = useTaskAction(task);
  const editable = ["open", "in_progress", "waiting", "paused"].includes(task.state) && (task.is_creator || task.can.manage);
  const chosen = new Set(task.tags.map((x) => x.id));
  return (
    <Card title={t("tasks.group_and_tags")}>
      <div className="row">
        <Field label={t("tasks.group")}>
          <select value={task.group_id ?? ""} disabled={!editable || pending} onChange={(e) => void run("group", { group_id: e.target.value || null })}>
            <option value="">—</option>
            {(groups.data?.groups ?? []).map((g) => (
              <option key={g.id} value={g.id}>
                {g.name}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <div className="small">
        {(tags.data?.tags ?? []).map((tag) => (
          <label key={tag.id} style={{ marginRight: 12 }}>
            <input
              type="checkbox"
              checked={chosen.has(tag.id)}
              disabled={!editable || pending}
              onChange={() => {
                const next = new Set(chosen);
                if (next.has(tag.id)) next.delete(tag.id);
                else next.add(tag.id);
                void run("tags", { tag_ids: [...next] });
              }}
            />{" "}
            #{tag.name}
          </label>
        ))}
        {!tags.data?.tags.length ? <span className="muted">{t("tasks.no_tags")}</span> : null}
      </div>
      <ErrorMessage error={error} />
    </Card>
  );
}

/** The repetition: «this and the following», pause, resume, stop (§ 19.4.2). */
export function SeriesCard({ task }: { task: WorkTask }) {
  const { t } = useTranslation();
  const company = useCompany();
  const queryClient = useQueryClient();
  const save = useCommand<Record<string, unknown>, unknown>(`/c/${company.public_id}/tasks/${task.id}/repeat`);
  const [title, setTitle] = useState(task.title);
  const s = task.series;
  if (!s) return null;
  const send = (body: Record<string, unknown>) =>
    save.run({ expected_version: s.version, ...body }).then(async (r) => {
      if (r) await queryClient.invalidateQueries({ queryKey: ["task"] });
    });
  const days = s.frequency === "weekly" ? s.weekdays.map((d) => t(`tasks.weekday_${d}`)).join(", ") : "";
  return (
    <Card title={t("tasks.repeat")}>
      <p className="small">
        {t(`tasks.frequency_${s.frequency}`)}
        {days ? ` · ${days}` : ""}
        {s.ends_on ? ` · ${t("tasks.repeat_until", { date: s.ends_on })}` : ""} · {t(`tasks.series_${s.state}`)}
        {s.next_on && s.state === "active" ? ` · ${t("tasks.next_on", { date: s.next_on })}` : ""}
      </p>
      {task.can.manage && s.state !== "stopped" ? (
        <>
          <div className="row">
            <Field label={t("tasks.title_following")}>
              <input value={title} onChange={(e) => setTitle(e.target.value)} />
            </Field>
            <div className="actions">
              <button type="button" className="secondary" disabled={save.pending || !title.trim()} onClick={() => void send({ action: "change", title })}>
                {t("tasks.change_following")}
              </button>
            </div>
          </div>
          <div className="actions">
            {s.state === "active" ? (
              <button type="button" className="secondary" disabled={save.pending} onClick={() => void send({ action: "pause" })}>
                {t("tasks.repeat_pause")}
              </button>
            ) : (
              <button type="button" className="secondary" disabled={save.pending} onClick={() => void send({ action: "resume" })}>
                {t("tasks.repeat_resume")}
              </button>
            )}
            <button type="button" className="danger" disabled={save.pending} onClick={() => void send({ action: "stop" })}>
              {t("tasks.repeat_stop")}
            </button>
          </div>
        </>
      ) : null}
      <ErrorMessage error={save.error} />
    </Card>
  );
}

/** Groups and tags of the company: created, renamed and removed with «manage» (§ 23.9.5). */
export function GroupsTagsAdmin() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const queryClient = useQueryClient();
  const groups = useGroups();
  const tags = useTags();
  const [groupName, setGroupName] = useState("");
  const [tagName, setTagName] = useState("");
  const base = `/c/${company.public_id}/tasks`;
  const addGroup = useCommand<Record<string, unknown>, unknown>(`${base}/groups`);
  const addTag = useCommand<Record<string, unknown>, unknown>(`${base}/tags`);
  const removeGroup = useCommand<{ id: string }, unknown>((b) => `${base}/groups/${b.id}`, "DELETE");
  const removeTag = useCommand<{ id: string }, unknown>((b) => `${base}/tags/${b.id}`, "DELETE");
  if (!can("tasks.manage")) return null;
  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["task-groups"] });
    await queryClient.invalidateQueries({ queryKey: ["task-tags"] });
  };
  return (
    <Card title={t("tasks.groups_and_tags")}>
      <div className="small">
        <strong>{t("tasks.groups")}:</strong>{" "}
        {(groups.data?.groups ?? []).map((g) => (
          <span key={g.id} style={{ marginRight: 12 }}>
            {g.name}{" "}
            <button type="button" className="link" onClick={() => void removeGroup.run({ id: g.id }).then((r) => (r ? refresh() : undefined))}>
              ×
            </button>
          </span>
        ))}
      </div>
      <div className="row">
        <Field label={t("tasks.new_group")}>
          <input value={groupName} onChange={(e) => setGroupName(e.target.value)} />
        </Field>
        <div className="actions">
          <button type="button" className="secondary" disabled={!groupName.trim() || addGroup.pending} onClick={() => void addGroup.run({ name: groupName }).then((r) => (r ? refresh().then(() => setGroupName("")) : undefined))}>
            {t("tasks.add")}
          </button>
        </div>
      </div>
      <div className="small">
        <strong>{t("tasks.tags")}:</strong>{" "}
        {(tags.data?.tags ?? []).map((tag) => (
          <span key={tag.id} style={{ marginRight: 12 }}>
            #{tag.name}{" "}
            <button type="button" className="link" onClick={() => void removeTag.run({ id: tag.id }).then((r) => (r ? refresh() : undefined))}>
              ×
            </button>
          </span>
        ))}
      </div>
      <div className="row">
        <Field label={t("tasks.new_tag")}>
          <input value={tagName} onChange={(e) => setTagName(e.target.value)} />
        </Field>
        <div className="actions">
          <button type="button" className="secondary" disabled={!tagName.trim() || addTag.pending} onClick={() => void addTag.run({ name: tagName }).then((r) => (r ? refresh().then(() => setTagName("")) : undefined))}>
            {t("tasks.add")}
          </button>
        </div>
      </div>
      <ErrorMessage error={addGroup.error ?? addTag.error ?? removeGroup.error ?? removeTag.error} />
    </Card>
  );
}
