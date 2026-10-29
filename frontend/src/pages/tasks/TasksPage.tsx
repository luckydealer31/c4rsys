import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router";

import { ApiError, uploadFile } from "../../api/client";
import { useApi } from "../../api/queries";
import type { WorkTask } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { deviceTimeZone, formatMoment } from "../../time";
import { useLocations } from "../settings/LocationsPage";
import { ChecklistCard, DependenciesCard, GroupsTagsAdmin, GroupTagsCard, SeriesCard, useGroups, useTags, WEEKDAYS } from "./Extensions";

type Result = { task: WorkTask };
export type TaskTarget = { objectType: string; objectId: string };

const PRIORITIES = ["low", "normal", "high", "critical"] as const;
const OBJECT_PATHS: Record<string, string> = {
  client: "/clients/",
  vehicle: "/vehicles/",
  order: "/orders/",
  rental: "/rentals/",
  service_operation: "/service/operations/",
  delivery: "/trips/",
  document: "/documents/",
  counterparty: "/clients/counterparties/",
};

function useRefresh() {
  const queryClient = useQueryClient();
  return async () => {
    await queryClient.invalidateQueries({ queryKey: ["tasks"] });
    await queryClient.invalidateQueries({ queryKey: ["task"] });
  };
}

function useWhen() {
  const { i18n } = useTranslation();
  const company = useCompany();
  const zone = company.timezone ?? deviceTimeZone();
  return (value: string | null) => formatMoment(value, zone, i18n.language);
}

/** A system task is named by its event in the user's language (§ 19.1.8). */
export function taskTitle(t: (key: string, options?: Record<string, unknown>) => string, x: WorkTask): string {
  return x.source === "system" ? t(`tasks.system_${x.title.replaceAll(".", "_")}`, { defaultValue: x.title }) : x.title;
}

function PriorityBadge({ priority }: { priority: WorkTask["priority"] }) {
  const { t } = useTranslation();
  if (priority === "normal") return null;
  const tone = priority === "critical" ? "danger" : priority === "high" ? "warn" : "";
  return <span className={`badge ${tone}`}>{t(`tasks.priority_${priority}`)}</span>;
}

function TaskList({ query, empty }: { query: string; empty?: string }) {
  const { t } = useTranslation();
  const company = useCompany();
  const when = useWhen();
  const list = useApi<{ tasks: WorkTask[] }>(["tasks", company.public_id, query], `/c/${company.public_id}/tasks?${query}`);
  const items = list.data?.tasks ?? [];
  if (list.isLoading) return <Loading />;
  if (!items.length) return <p className="muted">{empty ?? t("tasks.none")}</p>;
  return (
    <table>
      <tbody>
        {items.map((x) => (
          <tr key={x.id}>
            <td>
              <Link to={companyPath(company, `/tasks/${x.id}`)}>№ {x.number}</Link>
            </td>
            <td>
              {taskTitle(t, x)} <PriorityBadge priority={x.priority} />
              {x.overdue ? <span className="badge danger">{t("tasks.overdue")}</span> : null}
              {x.blocked ? <span className="badge warn">{t("tasks.blocked")}</span> : null}
              {x.tags.map((tag) => (
                <span key={tag.id} className="badge">
                  #{tag.name}
                </span>
              ))}
            </td>
            <td className="small">{t(`tasks.state_${x.state}`)}</td>
            <td className="small">{x.assignee_name ?? t("tasks.queue")}</td>
            <td className="small">{x.due_at ? when(x.due_at) : "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Work tasks: mine, the common queue, created by me and all of the area (§ 19.1, § 23.9). */
export function TasksPage() {
  const { t } = useTranslation();
  const can = useCan();
  const tabs = [
    ["mine", "view=mine", can("tasks.own")],
    ["queue", "view=queue", can("tasks.queue")],
    ["created", "view=created", can("tasks.create")],
    ["all", "", can("tasks.view_all") || can("tasks.manage")],
  ] as const;
  const visible = tabs.filter(([, , shown]) => shown);
  const [tab, setTab] = useState<string>(visible[0]?.[0] ?? "mine");
  const [show, setShow] = useState<"" | "overdue=1" | "closed=1">("");
  const [tag, setTag] = useState("");
  const [group, setGroup] = useState("");
  const [search, setSearch] = useState("");
  const groups = useGroups();
  const tags = useTags();
  const current = visible.find(([key]) => key === tab) ?? visible[0];
  const query = [
    current?.[1] ?? "",
    show,
    tag ? `tag=${tag}` : "",
    group ? `group=${group}` : "",
    search.trim() ? `q=${encodeURIComponent(search.trim())}` : "",
  ]
    .filter(Boolean)
    .join("&");
  return (
    <div className="page">
      <h1>{t("nav.tasks")}</h1>
      <div className="actions" style={{ marginTop: 0, marginBottom: 12 }}>
        {visible.map(([key]) => (
          <button key={key} type="button" className={key === current?.[0] ? "" : "secondary"} onClick={() => setTab(key)}>
            {t(`tasks.tab_${key}`)}
          </button>
        ))}
      </div>
      <div className="row" style={{ marginBottom: 12 }}>
        <Field label={t("tasks.show")}>
          <select value={show} onChange={(e) => setShow(e.target.value as typeof show)}>
            <option value="">{t("tasks.show_active")}</option>
            <option value="overdue=1">{t("tasks.show_overdue")}</option>
            <option value="closed=1">{t("tasks.show_closed")}</option>
          </select>
        </Field>
        <Field label={t("tasks.group")}>
          <select value={group} onChange={(e) => setGroup(e.target.value)}>
            <option value="">—</option>
            {(groups.data?.groups ?? []).map((g) => (
              <option key={g.id} value={g.id}>
                {g.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("tasks.tag")}>
          <select value={tag} onChange={(e) => setTag(e.target.value)}>
            <option value="">—</option>
            {(tags.data?.tags ?? []).map((x) => (
              <option key={x.id} value={x.id}>
                #{x.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("tasks.search")}>
          <input value={search} onChange={(e) => setSearch(e.target.value)} />
        </Field>
      </div>
      <Card>
        <TaskList query={query} />
      </Card>
      {can("tasks.create") ? <NewTask /> : null}
      <GroupsTagsAdmin />
      {can("company.settings") ? <TaskPolicyCard /> : null}
    </div>
  );
}

/** A new task: to oneself, to the queue or, with «manage», to another employee (§ 19.1.2). */
export function NewTask({ target, onDone }: { target?: TaskTarget; onDone?: () => void }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const navigate = useNavigate();
  const refresh = useRefresh();
  const locations = useLocations();
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [priority, setPriority] = useState("normal");
  const [assign, setAssign] = useState("self");
  const [location, setLocation] = useState("");
  const [dueDate, setDueDate] = useState("");
  const [dueTime, setDueTime] = useState("");
  const [checklist, setChecklist] = useState("");
  const [checklistRequired, setChecklistRequired] = useState(false);
  const [group, setGroup] = useState("");
  const [tagIds, setTagIds] = useState<string[]>([]);
  const [frequency, setFrequency] = useState("");
  const [reminders, setReminders] = useState<number[]>([]);
  const [weekdays, setWeekdays] = useState<number[]>([]);
  const [endsOn, setEndsOn] = useState("");
  const groups = useGroups();
  const tags = useTags();
  const create = useCommand<Record<string, unknown>, Result>(`/c/${company.public_id}/tasks`);
  const manage = can("tasks.manage");
  const people = useApi<{ employees: { id: string; name: string }[] }>(
    ["task-assignees", company.public_id, location],
    `/c/${company.public_id}/tasks/assignees${location ? `?location_id=${location}` : ""}`,
    manage,
  );

  async function submit(event: FormEvent) {
    event.preventDefault();
    const items = checklist
      .split("\n")
      .map((x) => x.trim())
      .filter(Boolean);
    const body: Record<string, unknown> = {
      title,
      description,
      priority,
      assign,
      due_date: dueDate || null,
      due_time: dueTime || null,
      checklist: items,
      checklist_required: checklistRequired && items.length > 0,
      group_id: group || null,
      tag_ids: tagIds,
      repeat: frequency ? { frequency, weekdays, ends_on: endsOn || null } : null,
      reminders: dueDate ? reminders : [],
    };
    // With an object the scope follows it (§ 19.1.7); without one the employee chooses.
    if (target) body.object = { object_type: target.objectType, object_id: target.objectId };
    else body.location_id = location || null;
    const result = await create.run(body);
    if (result) {
      setTitle("");
      setDescription("");
      await refresh();
      if (onDone) onDone();
      else navigate(companyPath(company, `/tasks/${result.task.id}`));
    }
  }

  return (
    <Card title={t("tasks.new")}>
      <form className="stack" onSubmit={submit}>
        <div className="row">
          <Field label={t("tasks.title")}>
            <input value={title} onChange={(e) => setTitle(e.target.value)} required />
          </Field>
          <Field label={t("tasks.priority")}>
            <select value={priority} onChange={(e) => setPriority(e.target.value)}>
              {PRIORITIES.map((p) => (
                <option key={p} value={p}>
                  {t(`tasks.priority_${p}`)}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <Field label={t("tasks.description")}>
          <textarea rows={3} value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>
        <div className="row">
          {!target ? (
            <Field label={t("tasks.scope")}>
              <select value={location} onChange={(e) => setLocation(e.target.value)}>
                <option value="">{t("tasks.whole_company")}</option>
                {(locations.data?.locations ?? []).map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.name}
                  </option>
                ))}
              </select>
            </Field>
          ) : null}
          <Field label={t("tasks.executor")}>
            <select value={assign} onChange={(e) => setAssign(e.target.value)}>
              <option value="self">{t("tasks.assign_self")}</option>
              <option value="queue">{t("tasks.assign_queue")}</option>
              {(people.data?.employees ?? []).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("tasks.due_date")} hint={t("tasks.due_hint")}>
            <input type="date" value={dueDate} onChange={(e) => setDueDate(e.target.value)} />
          </Field>
          <Field label={t("tasks.due_time")}>
            <input type="time" value={dueTime} disabled={!dueDate} onChange={(e) => setDueTime(e.target.value)} />
          </Field>
        </div>
        {dueDate ? <RemindersField value={reminders} onChange={setReminders} /> : null}
        <Field label={t("tasks.checklist")} hint={t("tasks.checklist_hint")}>
          <textarea rows={3} value={checklist} onChange={(e) => setChecklist(e.target.value)} />
        </Field>
        <label className="small">
          <input type="checkbox" checked={checklistRequired} onChange={() => setChecklistRequired(!checklistRequired)} /> {t("tasks.checklist_required")}
        </label>
        <div className="row">
          <Field label={t("tasks.group")}>
            <select value={group} onChange={(e) => setGroup(e.target.value)}>
              <option value="">—</option>
              {(groups.data?.groups ?? []).map((g) => (
                <option key={g.id} value={g.id}>
                  {g.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("tasks.repeat")} hint={frequency ? t("tasks.repeat_hint") : undefined}>
            <select value={frequency} onChange={(e) => setFrequency(e.target.value)}>
              <option value="">{t("tasks.no_repeat")}</option>
              <option value="daily">{t("tasks.frequency_daily")}</option>
              <option value="weekly">{t("tasks.frequency_weekly")}</option>
              <option value="monthly">{t("tasks.frequency_monthly")}</option>
            </select>
          </Field>
          {frequency ? (
            <Field label={t("tasks.repeat_end")}>
              <input type="date" value={endsOn} onChange={(e) => setEndsOn(e.target.value)} />
            </Field>
          ) : null}
        </div>
        {frequency === "weekly" ? (
          <div className="small">
            {WEEKDAYS.map((d) => (
              <label key={d} style={{ marginRight: 10 }}>
                <input
                  type="checkbox"
                  checked={weekdays.includes(d)}
                  onChange={() => setWeekdays(weekdays.includes(d) ? weekdays.filter((x) => x !== d) : [...weekdays, d])}
                />{" "}
                {t(`tasks.weekday_${d}`)}
              </label>
            ))}
          </div>
        ) : null}
        {(tags.data?.tags ?? []).length ? (
          <div className="small">
            {(tags.data?.tags ?? []).map((x) => (
              <label key={x.id} style={{ marginRight: 10 }}>
                <input
                  type="checkbox"
                  checked={tagIds.includes(x.id)}
                  onChange={() => setTagIds(tagIds.includes(x.id) ? tagIds.filter((y) => y !== x.id) : [...tagIds, x.id])}
                />{" "}
                #{x.name}
              </label>
            ))}
          </div>
        ) : null}
        <div className="actions">
          <button type="submit" disabled={create.pending || !title.trim() || (Boolean(frequency) && !dueDate)}>
            {t("tasks.create")}
          </button>
        </div>
        <ErrorMessage error={create.error} />
      </form>
    </Card>
  );
}

/** Tasks of a card: its main object or an additional link (§ 19.1.3). */
export function LinkedTasks({ objectType, objectId }: TaskTarget) {
  const { t } = useTranslation();
  const can = useCan();
  const [adding, setAdding] = useState(false);
  if (!["tasks.own", "tasks.queue", "tasks.create", "tasks.view_all", "tasks.manage"].some(can)) return null;
  return (
    <Card title={t("nav.tasks")}>
      <TaskList query={`object_type=${objectType}&object_id=${objectId}`} />
      {can("tasks.create") ? (
        adding ? (
          <NewTask target={{ objectType, objectId }} onDone={() => setAdding(false)} />
        ) : (
          <div className="actions">
            <button type="button" className="secondary" onClick={() => setAdding(true)}>
              {t("tasks.new")}
            </button>
          </div>
        )
      ) : null}
    </Card>
  );
}

/** A task: details, state, executor, deadline, comments and history (§ 19.1–19.2). */
export function TaskPage() {
  const { t } = useTranslation();
  const { id } = useParams();
  const company = useCompany();
  const refresh = useRefresh();
  const when = useWhen();
  const base = `/c/${company.public_id}/tasks/${id}`;
  const query = useApi<Result>(["task", company.public_id, id], base, Boolean(id));
  const act = useCommand<Record<string, unknown> & { action: string }, Result>((body) => `${base}/${body.action}`);
  const change = useCommand<Record<string, unknown>, Result>(base, "PATCH");
  const [note, setNote] = useState("");
  const [reason, setReason] = useState("");
  const [dueDate, setDueDate] = useState("");
  const [dueTime, setDueTime] = useState("");
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [uploadError, setUploadError] = useState<ApiError | null>(null);
  const x = query.data?.task;
  const people = useApi<{ employees: { id: string; name: string }[] }>(
    ["task-assignees", company.public_id, x?.location_id ?? ""],
    `/c/${company.public_id}/tasks/assignees${x?.location_id ? `?location_id=${x.location_id}` : ""}`,
    Boolean(x?.can.manage),
  );
  if (query.isLoading) return <Loading />;
  if (!x) return <ErrorMessage error={query.error instanceof ApiError ? query.error : null} />;
  const active = ["open", "in_progress", "waiting", "paused"].includes(x.state);
  const runner = x.is_mine || x.can.manage;
  const run = (action: string, body: Record<string, unknown> = {}) =>
    act.run({ action, expected_version: x.version, ...body }).then((r) => (r ? refresh() : undefined));

  async function addComment(event: FormEvent) {
    event.preventDefault();
    setUploadError(null);
    let fileId: string | null = null;
    if (file) {
      try {
        fileId = (await uploadFile("task_attachment", file)).id;
      } catch (caught) {
        setUploadError(caught instanceof ApiError ? caught : new ApiError(0, null));
        return;
      }
    }
    const result = await act.run({ action: "comment", text, file_id: fileId, file_name: file?.name ?? "" });
    if (result) {
      setText("");
      setFile(null);
      await refresh();
    }
  }

  return (
    <div className="page">
      <p>
        <Link to={companyPath(company, "/tasks")}>← {t("nav.tasks")}</Link>
      </p>
      <h1>
        {t("tasks.number", { number: x.number })} · {taskTitle(t, x)} <PriorityBadge priority={x.priority} />
        {x.overdue ? <span className="badge danger">{t("tasks.overdue")}</span> : null}
        {x.blocked ? <span className="badge warn">{t("tasks.blocked")}</span> : null}
      </h1>
      <Card>
        <dl className="facts">
          <dt>{t("tasks.state")}</dt>
          <dd>
            {t(`tasks.state_${x.state}`)}
            {x.state_note ? ` · ${x.state_note}` : ""}
            {x.cancel_reason ? ` · ${x.cancel_reason}` : ""}
          </dd>
          <dt>{t("tasks.executor")}</dt>
          <dd>{x.assignee_name ?? t("tasks.queue")}</dd>
          <dt>{t("tasks.due")}</dt>
          <dd>
            {x.due_at ? when(x.due_at) : "—"}
            {x.reminders.length ? ` · ${t("tasks.reminders")}: ${x.reminders.map((m) => reminderText(t, m)).join(", ")}` : ""}
          </dd>
          <dt>{t("tasks.created_by")}</dt>
          <dd>
            {x.created_by_name ?? (x.source === "system" ? t("tasks.source_system") : "—")} · {when(x.created_at)}
          </dd>
          {x.object_type && x.object_id ? (
            <>
              <dt>{t("tasks.object")}</dt>
              <dd>
                <Link to={companyPath(company, `${OBJECT_PATHS[x.object_type] ?? "/"}${x.object_id}`)}>{t(`documents.object_${x.object_type}`)}</Link>
              </dd>
            </>
          ) : null}
        </dl>
        {x.description ? <p style={{ whiteSpace: "pre-wrap" }}>{x.description}</p> : null}
      </Card>
      {active ? (
        <Card title={t("tasks.actions")}>
          <div className="actions" style={{ marginTop: 0 }}>
            {x.assignee_id === null && x.can.queue ? (
              <button type="button" disabled={act.pending} onClick={() => void run("take")}>
                {t("tasks.take")}
              </button>
            ) : null}
            {x.state === "open" && x.assignee_id && runner ? (
              <button type="button" disabled={act.pending} onClick={() => void run("start")}>
                {t("tasks.start")}
              </button>
            ) : null}
            {(x.state === "waiting" || x.state === "paused") && runner ? (
              <button type="button" disabled={act.pending} onClick={() => void run("resume")}>
                {t("tasks.resume")}
              </button>
            ) : null}
            {runner && x.source === "manual" ? (
              <button type="button" disabled={act.pending} onClick={() => void run("complete")}>
                {t("tasks.complete")}
              </button>
            ) : null}
            {x.assignee_id && (x.can.manage || (x.is_mine && x.policy.executor_may_release)) ? (
              <button type="button" className="secondary" disabled={act.pending} onClick={() => void run("release")}>
                {t("tasks.release")}
              </button>
            ) : null}
          </div>
          {x.state === "in_progress" && runner ? (
            <div className="row">
              <Field label={t("tasks.note")} hint={t("tasks.note_hint")}>
                <input value={note} onChange={(e) => setNote(e.target.value)} />
              </Field>
              <div className="actions">
                <button type="button" className="secondary" disabled={act.pending || !note.trim()} onClick={() => void run("wait", { note }).then(() => setNote(""))}>
                  {t("tasks.wait")}
                </button>
                <button type="button" className="secondary" disabled={act.pending || !note.trim()} onClick={() => void run("pause", { note }).then(() => setNote(""))}>
                  {t("tasks.pause")}
                </button>
              </div>
            </div>
          ) : null}
          {x.can.manage ? (
            <div className="row">
              <Field label={t("tasks.assign_to")}>
                <select value="" onChange={(e) => (e.target.value ? void run("assign", { assignee_id: e.target.value }) : undefined)}>
                  <option value="">—</option>
                  {(people.data?.employees ?? [])
                    .filter((p) => p.id !== x.assignee_id)
                    .map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                </select>
              </Field>
            </div>
          ) : null}
          {x.can.manage || (x.is_mine && x.policy.executor_may_change_due) ? (
            <div className="row">
              <Field label={t("tasks.due_date")}>
                <input type="date" value={dueDate} onChange={(e) => setDueDate(e.target.value)} />
              </Field>
              <Field label={t("tasks.due_time")}>
                <input type="time" value={dueTime} disabled={!dueDate} onChange={(e) => setDueTime(e.target.value)} />
              </Field>
              <div className="actions">
                <button
                  type="button"
                  className="secondary"
                  disabled={change.pending}
                  onClick={() =>
                    void change
                      .run({ expected_version: x.version, due_date: dueDate || null, due_time: dueTime || null })
                      .then((r) => (r ? refresh() : undefined))
                  }
                >
                  {dueDate ? t("tasks.change_due") : t("tasks.remove_due")}
                </button>
              </div>
            </div>
          ) : null}
          {x.can.manage || (x.is_creator && x.state === "open") ? (
            <div className="row">
              <Field label={t("tasks.cancel_reason")}>
                <input value={reason} onChange={(e) => setReason(e.target.value)} />
              </Field>
              <div className="actions">
                <button type="button" className="danger" disabled={act.pending} onClick={() => void run("cancel", { reason })}>
                  {t("tasks.cancel")}
                </button>
              </div>
            </div>
          ) : null}
          <ErrorMessage error={act.error ?? change.error} />
        </Card>
      ) : null}
      <ChecklistCard task={x} />
      <DependenciesCard task={x} />
      <GroupTagsCard task={x} />
      <SeriesCard task={x} />
      <Card title={t("tasks.comments")}>
        {x.comments.map((c) => (
          <div key={c.id} className="small" style={{ marginBottom: 6 }}>
            <strong>{c.author}</strong> · {when(c.created_at)}
            {c.text ? <div style={{ whiteSpace: "pre-wrap" }}>{c.text}</div> : null}
            {c.file_id ? (
              <a href={`/api/files/${c.file_id}`} target="_blank" rel="noreferrer">
                {c.file_name || t("documents.open_file")}
              </a>
            ) : null}
          </div>
        ))}
        <form className="row" onSubmit={addComment}>
          <Field label={t("tasks.comment")}>
            <textarea rows={2} value={text} onChange={(e) => setText(e.target.value)} />
          </Field>
          <Field label={t("tasks.attachment")} hint={t("documents.file_hint")}>
            <input type="file" accept=".pdf,.docx,.xlsx,image/jpeg,image/png,image/webp" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </Field>
          <div className="actions">
            <button type="submit" className="secondary" disabled={act.pending || (!text.trim() && !file)}>
              {t("tasks.add_comment")}
            </button>
          </div>
          <ErrorMessage error={uploadError} />
        </form>
      </Card>
      <Card title={t("tasks.history")}>
        {x.history.map((e) => (
          <div key={e.id} className="small">
            {when(e.occurred_at)} · {e.actor.name || t("tasks.source_system")} ·{" "}
            {t(`actions.${e.action.replaceAll(".", "_")}`, { defaultValue: e.action })}
            {e.reason ? ` · ${e.reason}` : ""}
          </div>
        ))}
      </Card>
    </div>
  );
}

type Policy = { executor_may_release: boolean; executor_may_change_due: boolean; overdue_repeat_hours: number; version: number };

/** The company's rules for executors (decision З-2). */
function TaskPolicyCard() {
  const company = useCompany();
  const query = useApi<{ policy: Policy }>(["task-policy", company.public_id], `/c/${company.public_id}/tasks/policy`);
  if (!query.data) return <Loading />;
  return <TaskPolicyFields key={query.data.policy.version} policy={query.data.policy} />;
}

function TaskPolicyFields({ policy }: { policy: Policy }) {
  const { t } = useTranslation();
  const company = useCompany();
  const queryClient = useQueryClient();
  const [release, setRelease] = useState(policy.executor_may_release);
  const [due, setDue] = useState(policy.executor_may_change_due);
  const [hours, setHours] = useState(String(policy.overdue_repeat_hours));
  const save = useCommand<Record<string, unknown>, { policy: Policy }>(`/c/${company.public_id}/tasks/policy`, "PUT");
  return (
    <Card title={t("tasks.policy_title")}>
      <label className="small" style={{ display: "block" }}>
        <input type="checkbox" checked={release} onChange={() => setRelease(!release)} /> {t("tasks.policy_release")}
      </label>
      <label className="small" style={{ display: "block" }}>
        <input type="checkbox" checked={due} onChange={() => setDue(!due)} /> {t("tasks.policy_change_due")}
      </label>
      <Field label={t("tasks.policy_overdue_repeat")} hint={t("tasks.policy_overdue_repeat_hint")}>
        <input value={hours} inputMode="numeric" onChange={(e) => setHours(e.target.value.replace(/\D/g, ""))} />
      </Field>
      <div className="actions">
        <button
          type="button"
          disabled={save.pending}
          onClick={() =>
            void save
              .run({
                executor_may_release: release,
                executor_may_change_due: due,
                overdue_repeat_hours: Number(hours || "0"),
                expected_version: policy.version,
              })
              .then((r) => (r ? queryClient.invalidateQueries({ queryKey: ["task-policy"] }) : undefined))
          }
        >
          {t("common.save")}
        </button>
      </div>
      <ErrorMessage error={save.error} />
    </Card>
  );
}

const UNITS = [
  ["minutes", 1],
  ["hours", 60],
  ["days", 1440],
] as const;

function reminderText(t: (key: string, options?: Record<string, unknown>) => string, minutes: number): string {
  const [unit, size] = [...UNITS].reverse().find(([, n]) => minutes % n === 0) ?? UNITS[0];
  return t(`tasks.before_${unit}`, { count: minutes / size });
}

/** Up to 5 reminders «N minutes / hours / days before» the deadline (decision З-4). */
function RemindersField({ value, onChange }: { value: number[]; onChange: (next: number[]) => void }) {
  const { t } = useTranslation();
  const [amount, setAmount] = useState("1");
  const [unit, setUnit] = useState<(typeof UNITS)[number][0]>("hours");
  const minutes = Number(amount || "0") * (UNITS.find(([u]) => u === unit)?.[1] ?? 1);
  return (
    <div className="small">
      <strong>{t("tasks.reminders")}:</strong>{" "}
      {value.map((m) => (
        <span key={m} style={{ marginRight: 8 }}>
          {reminderText(t, m)}{" "}
          <button type="button" className="link" onClick={() => onChange(value.filter((x) => x !== m))}>
            ×
          </button>
        </span>
      ))}
      {value.length < 5 ? (
        <span>
          <input style={{ width: 60 }} value={amount} inputMode="numeric" onChange={(e) => setAmount(e.target.value.replace(/\D/g, ""))} />{" "}
          <select value={unit} onChange={(e) => setUnit(e.target.value as typeof unit)}>
            {UNITS.map(([u]) => (
              <option key={u} value={u}>
                {t(`tasks.unit_${u}`)}
              </option>
            ))}
          </select>{" "}
          <button type="button" className="link" disabled={!minutes || value.includes(minutes)} onClick={() => onChange([...value, minutes])}>
            {t("tasks.add_reminder")}
          </button>
        </span>
      ) : null}
    </div>
  );
}
