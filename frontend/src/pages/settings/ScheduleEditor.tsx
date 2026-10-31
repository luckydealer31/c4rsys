import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import type { ApiError } from "../../api/client";
import { api } from "../../api/client";
import type { Location, Schedule, ScheduleConflict, ScheduleInterval } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { ErrorMessage } from "../../components/ui";
import { companyPath, useCompany } from "../../layout/CompanyShell";
import { formatMoment } from "../../time";

const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;
// HH:MM, «24:00» allowed as the end of a day, so a plain text field (not type="time").
const TIME_INPUT = { width: "5.5em" } as const;
type Result = { location: Location; conflicts: ScheduleConflict[] };

function copy(schedule: Schedule): Schedule {
  return {
    weekly: Object.fromEntries(DAYS.map((d) => [d, (schedule.weekly[d] ?? []).map((x) => ({ ...x }))])),
    exceptions: schedule.exceptions.map((e) => ({ date: e.date, intervals: e.intervals.map((x) => ({ ...x })) })),
  };
}

export function scheduleSummary(schedule: Schedule): "24_7" | "custom" {
  const all = DAYS.every((d) => {
    const day = schedule.weekly[d] ?? [];
    return day.length === 1 && day[0]?.from === "00:00" && day[0]?.to === "24:00";
  });
  return all && schedule.exceptions.length === 0 ? "24_7" : "custom";
}

/** Intervals of one day; empty — closed. */
function Intervals({
  value,
  onChange,
}: {
  value: ScheduleInterval[];
  onChange: (next: ScheduleInterval[]) => void;
}) {
  const { t } = useTranslation();
  return (
    <span style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
      {value.length === 0 ? <span className="muted">{t("locations.schedule_closed")}</span> : null}
      {value.map((interval, i) => (
        <span key={i} style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
          <input
            aria-label="from"
            value={interval.from}
            style={TIME_INPUT}
            onChange={(e) => onChange(value.map((x, j) => (j === i ? { ...x, from: e.target.value } : x)))}
          />
          –
          <input
            aria-label="to"
            value={interval.to}
            style={TIME_INPUT}
            onChange={(e) => onChange(value.map((x, j) => (j === i ? { ...x, to: e.target.value } : x)))}
          />{" "}
          <button type="button" className="link" onClick={() => onChange(value.filter((_, j) => j !== i))}>
            {t("locations.schedule_remove")}
          </button>
        </span>
      ))}
      <button type="button" className="link" onClick={() => onChange([...value, { from: "09:00", to: "18:00" }])}>
        {t("locations.schedule_add_interval")}
      </button>
    </span>
  );
}

function ScheduleForm({
  initial,
  pending,
  error,
  onSave,
  onCancel,
}: {
  initial: Schedule;
  pending: boolean;
  error: ApiError | null;
  onSave: (schedule: Schedule) => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<Schedule>(() => copy(initial));
  const [date, setDate] = useState("");
  return (
    <div className="stack">
      <table>
        <tbody>
          {DAYS.map((d, i) => (
            <tr key={d}>
              <td>{t(`tasks.weekday_${i + 1}`)}</td>
              <td>
                <Intervals
                  value={draft.weekly[d] ?? []}
                  onChange={(next) => setDraft({ ...draft, weekly: { ...draft.weekly, [d]: next } })}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <strong>{t("locations.schedule_exceptions")}</strong>
      <table>
        <tbody>
          {draft.exceptions.map((e, i) => (
            <tr key={e.date}>
              <td>{e.date}</td>
              <td>
                <Intervals
                  value={e.intervals}
                  onChange={(next) =>
                    setDraft({
                      ...draft,
                      exceptions: draft.exceptions.map((x, j) => (j === i ? { ...x, intervals: next } : x)),
                    })
                  }
                />
              </td>
              <td>
                <button
                  type="button"
                  className="link"
                  onClick={() => setDraft({ ...draft, exceptions: draft.exceptions.filter((_, j) => j !== i) })}
                >
                  {t("locations.schedule_remove")}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="actions">
        <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        <button
          type="button"
          className="secondary"
          disabled={!date || draft.exceptions.some((e) => e.date === date)}
          onClick={() => {
            const exceptions = [...draft.exceptions, { date, intervals: [] }];
            exceptions.sort((a, b) => a.date.localeCompare(b.date));
            setDraft({ ...draft, exceptions });
            setDate("");
          }}
        >
          {t("locations.schedule_add_exception")}
        </button>
      </div>
      <ErrorMessage error={error} />
      <div className="actions">
        <button type="button" disabled={pending} onClick={() => onSave(draft)}>
          {t("locations.schedule_save")}
        </button>
        <button type="button" className="secondary" onClick={onCancel}>
          {t("common.cancel")}
        </button>
      </div>
    </div>
  );
}

function Conflicts({ conflicts, timeZone }: { conflicts: ScheduleConflict[]; timeZone: string }) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  if (conflicts.length === 0) return <p className="muted small">{t("locations.schedule_conflicts_none")}</p>;
  return (
    <div className="alert warn" role="status">
      <strong>{t("locations.schedule_conflicts")}</strong>
      <ul>
        {conflicts.map((c) => (
          <li key={`${c.object_id}-${c.role}`}>
            <Link
              to={companyPath(
                company,
                c.object_type === "rental" ? `/rentals/${c.object_id}` : `/service/operations/${c.object_id}`,
              )}
            >
              {t(`locations.conflict_${c.object_type}`, { number: c.number })}
            </Link>{" "}
            — {t(`locations.conflict_role_${c.role}`)}, {formatMoment(c.at, timeZone, i18n.language)}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** § 4.2.3, § 4.2.4, § 22.1.6: the schedule of the location, the source of each context's
 * schedule with «own schedule» and «restore inheritance», and the future operations that fall
 * outside the schedules in force. */
export function ScheduleEditor({ location, editable }: { location: Location; editable: boolean }) {
  const { t } = useTranslation();
  const company = useCompany();
  const client = useQueryClient();
  const base = `/c/${company.public_id}/locations/${location.id}`;
  const timeZone = location.timezone ?? company.timezone ?? "UTC";
  const [editing, setEditing] = useState<string | null>(null);
  const [conflicts, setConflicts] = useState<ScheduleConflict[] | null>(null);
  const setLocation = useCommand<Schedule & { expected_version: number }, Result>(`${base}/schedule`, "PUT");
  const setContext = useCommand<Schedule & { id: string; expected_version: number }, Result>(
    (body) => `${base}/contexts/${body.id}/schedule`,
    "PUT",
  );
  const inherit = useCommand<{ id: string; expected_version: number }, Result>(
    (body) => `${base}/contexts/${body.id}/schedule`,
    "DELETE",
  );

  async function done(result: Result | null) {
    if (!result) return;
    setEditing(null);
    setConflicts(result.conflicts);
    await client.invalidateQueries({ queryKey: ["locations", company.public_id] });
  }

  async function check() {
    const found = await api<{ conflicts: ScheduleConflict[] }>(`${base}/schedule`);
    setConflicts(found.conflicts);
  }

  const summary = (s: Schedule) =>
    scheduleSummary(s) === "24_7" ? t("locations.schedule_24_7") : t("locations.schedule_custom");

  return (
    <div className="stack">
      <h3>{t("locations.schedule_title")}</h3>
      <p className="muted small">{t("locations.schedule_hint")}</p>
      <p>
        {t("locations.schedule")}: {summary(location.schedule)}{" "}
        {editable && editing !== "location" ? (
          <button type="button" className="link" onClick={() => setEditing("location")}>
            {t("locations.schedule_edit")}
          </button>
        ) : null}
      </p>
      {editing === "location" ? (
        <ScheduleForm
          initial={location.schedule}
          pending={setLocation.pending}
          error={setLocation.error}
          onCancel={() => setEditing(null)}
          onSave={(s) => void setLocation.run({ ...s, expected_version: location.schedule.version }).then(done)}
        />
      ) : null}
      <table>
        <tbody>
          {location.contexts.map((c) => (
            <tr key={c.id}>
              <td>{t(`contexts.${c.type}`)}</td>
              <td>
                {c.schedule.source === "own" && c.schedule.own
                  ? `${t("locations.schedule_source_own")}: ${summary(c.schedule.own)}`
                  : t("locations.schedule_source_location")}
              </td>
              <td>
                {editable ? (
                  <>
                    <button type="button" className="link" onClick={() => setEditing(c.id)}>
                      {c.schedule.source === "own" ? t("locations.schedule_edit") : t("locations.schedule_set_own")}
                    </button>{" "}
                    {c.schedule.source === "own" ? (
                      <button
                        type="button"
                        className="link"
                        disabled={inherit.pending}
                        onClick={() =>
                          void inherit.run({ id: c.id, expected_version: c.schedule.version }).then(done)
                        }
                      >
                        {t("locations.schedule_inherit")}
                      </button>
                    ) : null}
                  </>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <ErrorMessage error={inherit.error} />
      {location.contexts.map((c) =>
        editing === c.id ? (
          <ScheduleForm
            key={c.id}
            initial={c.schedule.own ?? location.schedule}
            pending={setContext.pending}
            error={setContext.error}
            onCancel={() => setEditing(null)}
            onSave={(s) => void setContext.run({ ...s, id: c.id, expected_version: c.schedule.version }).then(done)}
          />
        ) : null,
      )}
      {editable ? (
        <div className="actions">
          <button type="button" className="secondary" onClick={() => void check()}>
            {t("locations.schedule_conflicts_check")}
          </button>
        </div>
      ) : null}
      {conflicts ? <Conflicts conflicts={conflicts} timeZone={timeZone} /> : null}
    </div>
  );
}
