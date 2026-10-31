import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router";

import { ApiError } from "../../api/client";
import { useApi } from "../../api/queries";
import type { ServiceJob, ServiceMaterial, ServiceOperation } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { formatMoment, fromLocalInput, toLocalInput } from "../../time";
import { useLocations } from "../settings/LocationsPage";
import { LinkedTasks } from "../tasks/TasksPage";
import { LinkedDocuments } from "../documents/DocumentsPage";
import { StockAdder } from "../warehouse/Sales";
import { FixedDamagesPicker, ServiceInspectionsCard, type FixedChoice } from "../inspections/Blocks";
import {
  FINAL_STATES,
  HOLDING_STATES,
  OPERATION_KEYS,
  price,
  useContractors,
  useExecutors,
  useServiceOperation,
  useServicePosts,
  useServiceWorks,
  workTerms,
  zoneOf,
} from "./hooks";

type Result = { operation: ServiceOperation };

function useRefresh() {
  const queryClient = useQueryClient();
  return async () => {
    for (const key of OPERATION_KEYS) await queryClient.invalidateQueries({ queryKey: [key] });
  };
}

/** A service operation: plan, acceptance, place, works and their results (§ 16.2–16.4). */
export function ServiceOperationPage() {
  const { t, i18n } = useTranslation();
  const { id } = useParams();
  const company = useCompany();
  const can = useCan();
  const locations = useLocations();
  const query = useServiceOperation(id);
  const op = query.data?.operation;
  if (query.isLoading) return <Loading />;
  if (!op) return <ErrorMessage error={query.error instanceof ApiError ? query.error : null} />;
  const location = locations.data?.locations.find((l) => l.id === op.location_id);
  const zone = zoneOf(location, company);
  const when = (value: string | null) => formatMoment(value, zone, i18n.language);
  const final = FINAL_STATES.includes(op.state);
  return (
    <div className="page">
      <p>
        <Link to={companyPath(company, "/service")}>← {t("nav.service")}</Link>
      </p>
      <h1>
        {t("service.operation_title", { number: op.number })}{" "}
        <span className={`badge ${final ? "" : "warn"}`}>{t(`service.state_${op.state}`)}</span>
      </h1>
      <Card>
        <dl className="facts">
          <dt>{t("service.vehicle")}</dt>
          <dd>
            <Link to={companyPath(company, `/vehicles/${op.vehicle_card_id}`)}>{op.vehicle_name ?? "—"}</Link>
          </dd>
          <dt>{t("service.order")}</dt>
          <dd>
            <Link to={companyPath(company, `/orders/${op.order_id}`)}>
              {t("service.order_number", { number: op.order_number })}
            </Link>
            {op.is_internal ? ` · ${t("orders.internal_order")}` : ""}
          </dd>
          {op.location_id ? (
            <>
              <dt>{t("service.location")}</dt>
              <dd>{location?.name ?? "—"}</dd>
            </>
          ) : null}
          {op.counterparty_name ? (
            <>
              <dt>{t("service.contractor")}</dt>
              <dd>{op.counterparty_name}</dd>
            </>
          ) : null}
          <dt>{t("service.reason")}</dt>
          <dd>{op.reason}</dd>
          <dt>{t("service.plan")}</dt>
          <dd>{op.planned_start ? `${when(op.planned_start)} — ${when(op.planned_end)}` : t("service.no_plan")}</dd>
          {op.location_id ? (
            <>
              <dt>{t("service.place")}</dt>
              <dd>{op.post_name ?? t("service.general_zone")}</dd>
            </>
          ) : null}
          {op.accepted_at ? (
            <>
              <dt>{t("service.accepted_at")}</dt>
              <dd>{when(op.accepted_at)}</dd>
            </>
          ) : null}
          {op.finished_at ? (
            <>
              <dt>{t("service.finished_at")}</dt>
              <dd>{when(op.finished_at)}</dd>
            </>
          ) : null}
          {op.end_reason ? (
            <>
              <dt>{t("service.end_reason")}</dt>
              <dd>{op.end_reason}</dd>
            </>
          ) : null}
        </dl>
        {(op.blockers ?? []).map((b) => (
          <p key={b} className="alert">
            {t(`service.blocker_${b}`)}
          </p>
        ))}
      </Card>
      <ServiceInspectionsCard op={op} />
      <Jobs op={op} zone={zone} />
      <Handovers op={op} zone={zone} />
      <LinkedDocuments objectType="service_operation" objectId={op.id} />
      <LinkedTasks objectType="service_operation" objectId={op.id} />
      {!final ? <OperationActions op={op} zone={zone} /> : null}
      {op.state === "works_done" && can("service.reopen") ? <Reopen op={op} /> : null}
    </div>
  );
}

function OperationActions({ op, zone }: { op: ServiceOperation; zone: string }) {
  const can = useCan();
  const holding = HOLDING_STATES.includes(op.state);
  return (
    <>
      {can("service.create") ? <AddJob op={op} /> : null}
      {op.location_id && op.state === "planned" && can("service.accept") ? <Accept op={op} zone={zone} /> : null}
      {(op.state === "planned" || op.state === "accepted") && can("service.create") ? <Plan op={op} zone={zone} /> : null}
      {op.location_id && op.accepted_at && holding && can("service.place") ? <Place op={op} /> : null}
      {op.state !== "planned" && can("service.complete") ? <Finish op={op} /> : null}
      {can("service.cancel") ? <End op={op} /> : null}
    </>
  );
}

function usePosts(op: ServiceOperation) {
  const posts = useServicePosts();
  return (posts.data?.posts ?? []).filter((p) => p.location_id === op.location_id && p.state === "active");
}

function opCommand(cmp: string, op: ServiceOperation, action: string) {
  return `/c/${cmp}/service/operations/${op.id}/${action}`;
}

function Accept({ op, zone }: { op: ServiceOperation; zone: string }) {
  const { t } = useTranslation();
  const company = useCompany();
  const refresh = useRefresh();
  const posts = usePosts(op);
  const [at, setAt] = useState(toLocalInput(new Date(), zone));
  const [postId, setPostId] = useState(op.post_id ?? "");
  const accept = useCommand<Record<string, unknown>, Result>(opCommand(company.public_id, op, "accept"));
  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await accept.run({ accepted_at: fromLocalInput(at, zone), post_id: postId || null, expected_version: op.version });
    if (result) await refresh();
  }
  return (
    <Card title={t("service.accept_title")}>
      <p className="muted small">{t("service.accept_hint")}</p>
      <form className="row" onSubmit={submit}>
        <Field label={t("service.accepted_at")}>
          <input type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} />
        </Field>
        <PostSelect posts={posts} value={postId} onChange={setPostId} />
        <div className="actions">
          <button type="submit" disabled={accept.pending}>
            {t("service.accept")}
          </button>
        </div>
      </form>
      <ErrorMessage error={accept.error} />
    </Card>
  );
}

function PostSelect({
  posts,
  value,
  onChange,
}: {
  posts: { id: string; name: string }[];
  value: string;
  onChange: (value: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <Field label={t("service.post")}>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{t("service.general_zone")}</option>
        {posts.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>
    </Field>
  );
}

/** Until the service starts its planned time and post change (§ 16.2.6). */
function Plan({ op, zone }: { op: ServiceOperation; zone: string }) {
  const { t } = useTranslation();
  const company = useCompany();
  const refresh = useRefresh();
  const posts = usePosts(op);
  const [start, setStart] = useState(op.planned_start ? toLocalInput(new Date(op.planned_start), zone) : "");
  const [end, setEnd] = useState(op.planned_end ? toLocalInput(new Date(op.planned_end), zone) : "");
  const [postId, setPostId] = useState(op.post_id ?? "");
  const plan = useCommand<Record<string, unknown>, Result>(opCommand(company.public_id, op, "plan"));
  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await plan.run({
      planned_start: start ? fromLocalInput(start, zone) : null,
      planned_end: end ? fromLocalInput(end, zone) : null,
      post_id: postId || null,
      expected_version: op.version,
    });
    if (result) await refresh();
  }
  return (
    <Card title={t("service.plan_title")}>
      <form className="row" onSubmit={submit}>
        <Field label={t("service.planned_start")}>
          <input type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} />
        </Field>
        <Field label={t("service.planned_end")}>
          <input type="datetime-local" value={end} onChange={(e) => setEnd(e.target.value)} />
        </Field>
        <PostSelect posts={posts} value={postId} onChange={setPostId} />
        <div className="actions">
          <button type="submit" className="secondary" disabled={plan.pending}>
            {t("common.save")}
          </button>
        </div>
      </form>
      <ErrorMessage error={plan.error} />
    </Card>
  );
}

/** Between a post and the general zone; leaving the post frees it (§ 16.3.4–16.3.5). */
function Place({ op }: { op: ServiceOperation }) {
  const { t } = useTranslation();
  const company = useCompany();
  const refresh = useRefresh();
  const posts = usePosts(op);
  const [postId, setPostId] = useState(op.post_id ?? "");
  const place = useCommand<Record<string, unknown>, Result>(opCommand(company.public_id, op, "place"));
  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await place.run({ post_id: postId || null, expected_version: op.version });
    if (result) await refresh();
  }
  return (
    <Card title={t("service.place_title")}>
      <form className="row" onSubmit={submit}>
        <PostSelect posts={posts} value={postId} onChange={setPostId} />
        <div className="actions">
          <button type="submit" className="secondary" disabled={place.pending}>
            {t("service.move")}
          </button>
        </div>
      </form>
      <ErrorMessage error={place.error} />
    </Card>
  );
}

function Finish({ op }: { op: ServiceOperation }) {
  const { t } = useTranslation();
  const company = useCompany();
  const refresh = useRefresh();
  const finish = useCommand<Record<string, unknown>, Result>(opCommand(company.public_id, op, "finish"));
  return (
    <Card title={t("service.finish_title")}>
      <p className="muted small">{t("service.finish_hint")}</p>
      <ErrorMessage error={finish.error} />
      <div className="actions">
        <button
          type="button"
          disabled={finish.pending}
          onClick={() => void finish.run({ expected_version: op.version }).then((r) => (r ? refresh() : undefined))}
        >
          {t("service.finish")}
        </button>
      </div>
    </Card>
  );
}

/** A service finished by mistake is opened again by a separate right (§ 16.8.2): works may be
 * added or corrected and it is finished again; the done works keep their results. */
function Reopen({ op }: { op: ServiceOperation }) {
  const { t } = useTranslation();
  const company = useCompany();
  const refresh = useRefresh();
  const [reason, setReason] = useState("");
  const reopen = useCommand<Record<string, unknown>, Result>(opCommand(company.public_id, op, "reopen"));
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (await reopen.run({ reason, expected_version: op.version })) {
      setReason("");
      await refresh();
    }
  }
  return (
    <Card title={t("service.reopen_title")}>
      <p className="muted small">{t("service.reopen_hint")}</p>
      <form className="row" onSubmit={submit}>
        <Field label={t("service.reopen_reason")}>
          <input value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
        <div className="actions">
          <button type="submit" className="secondary" disabled={reopen.pending || !reason.trim()}>
            {t("service.reopen")}
          </button>
        </div>
      </form>
      <ErrorMessage error={reopen.error} />
    </Card>
  );
}

/** «Cancel» before any start, «Terminate» after one (§ 16.4.8). */
function End({ op }: { op: ServiceOperation }) {
  const { t } = useTranslation();
  const company = useCompany();
  const refresh = useRefresh();
  const [reason, setReason] = useState("");
  const started = op.state === "in_progress";
  const end = useCommand<Record<string, unknown>, Result>(opCommand(company.public_id, op, started ? "terminate" : "cancel"));
  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await end.run({ reason, expected_version: op.version });
    if (result) await refresh();
  }
  return (
    <Card title={started ? t("service.terminate_title") : t("service.cancel_title")}>
      <p className="muted small">{started ? t("service.terminate_hint") : t("service.cancel_hint")}</p>
      <form className="row" onSubmit={submit}>
        <Field label={t("service.end_reason")}>
          <input value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
        <div className="actions">
          <button type="submit" className="danger" disabled={end.pending || !reason.trim()}>
            {started ? t("service.terminate") : t("service.cancel")}
          </button>
        </div>
      </form>
      <ErrorMessage error={end.error} />
    </Card>
  );
}

function AddJob({ op }: { op: ServiceOperation }) {
  const { t } = useTranslation();
  const company = useCompany();
  const refresh = useRefresh();
  const can = useCan();
  const works = useServiceWorks();
  const contractors = useContractors(can("service.external"));
  const [workId, setWorkId] = useState("");
  const [contractorId, setContractorId] = useState("");
  const add = useCommand<Record<string, unknown>, Result>(opCommand(company.public_id, op, "jobs"));
  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await add.run({ work_id: workId, contractor_id: contractorId || null, expected_version: op.version });
    if (result) {
      setWorkId("");
      await refresh();
    }
  }
  return (
    <Card title={t("service.add_job")}>
      <form className="row" onSubmit={submit}>
        <Field label={t("service.work")}>
          <select value={workId} onChange={(e) => setWorkId(e.target.value)}>
            <option value="">—</option>
            {(works.data?.works ?? [])
              .filter((w) => w.state === "active")
              .map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name} · {workTerms(w, t)}
                </option>
              ))}
          </select>
        </Field>
        {can("service.external") && op.location_id ? (
          <Field label={t("service.performer")}>
            <select value={contractorId} onChange={(e) => setContractorId(e.target.value)}>
              <option value="">{t("service.own_staff")}</option>
              {(contractors.data?.counterparties ?? []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </Field>
        ) : null}
        <div className="actions">
          <button type="submit" className="secondary" disabled={add.pending || !workId}>
            {t("service.add")}
          </button>
        </div>
      </form>
      <ErrorMessage error={add.error} />
    </Card>
  );
}

function Jobs({ op, zone }: { op: ServiceOperation; zone: string }) {
  const { t, i18n } = useTranslation();
  const when = (value: string | null) => formatMoment(value, zone, i18n.language);
  return (
    <Card title={t("service.works")}>
      {op.jobs.length === 0 ? <p className="muted">{t("service.no_jobs")}</p> : null}
      {op.jobs.map((job) => (
        <div key={job.id} className="list-link">
          <strong>{job.name}</strong>{" "}
          <span className={`badge ${job.state === "done" ? "ok" : job.state === "in_progress" ? "warn" : ""}`}>
            {t(`service.job_${job.state}`)}
          </span>
          <div className="small">
            {price(job.price, job.currency, t)}
            {job.pricing === "hours" && job.hours ? ` · ${t("service.terms_hours", { hours: Number(job.hours) })}` : ""}
            {" · "}
            {job.contractor_id
              ? `${t("service.contractor")}: ${job.contractor_name ?? "—"}`
              : (job.executor_name ?? t("service.no_executor"))}
            {job.contractor_cost !== null ? ` · ${t("service.contractor_cost_value", { cost: Number(job.contractor_cost), currency: job.currency })}` : ""}
          </div>
          {job.actual_start ? (
            <div className="muted small">
              {when(job.actual_start)}
              {job.actual_end ? ` — ${when(job.actual_end)}` : ""}
              {job.odometer ? ` · ${t("service.odometer_value", { value: Number(job.odometer) })}` : ""}
            </div>
          ) : null}
          {job.comment ? <div className="small">{job.comment}</div> : null}
          {job.end_reason ? <div className="muted small">{job.end_reason}</div> : null}
          <Materials op={op} job={job} />
          <JobActions op={op} job={job} zone={zone} />
        </div>
      ))}
    </Card>
  );
}

type Mode = "" | "start" | "complete" | "terminate" | "cancel" | "price" | "assign" | "contractor-cost" | "correct" | "open";

function JobActions({ op, job, zone }: { op: ServiceOperation; job: ServiceJob; zone: string }) {
  const { t } = useTranslation();
  const can = useCan();
  const [mode, setMode] = useState<Mode>("");
  const holding = HOLDING_STATES.includes(op.state);
  const orderOpen = op.order_state !== "closed" && op.order_state !== "cancelled";
  const own = job.contractor_id === null;
  const finished = job.state === "done" || job.state === "terminated";
  const buttons: [Mode | "take" | "close", string, boolean][] = [
    ["take", t("service.take"), own && job.state === "planned" && !job.executor_id && can("service.work")],
    ["assign", t("service.assign"), own && (job.state === "planned" || job.state === "in_progress") && can("service.assign")],
    ["start", t("service.start"), job.state === "planned" && (holding || !own) && !FINAL_STATES.includes(op.state) && can("service.work")],
    ["complete", t("service.complete"), job.state === "in_progress" && can("service.work")],
    ["terminate", t("service.terminate_job"), job.state === "in_progress" && can("service.work")],
    ["cancel", t("service.cancel_job"), job.state === "planned" && can("service.cancel")],
    ["price", t("service.change_price"), job.state !== "cancelled" && orderOpen && can("service.price")],
    ["contractor-cost", t("service.contractor_cost"), !own && job.state !== "cancelled" && orderOpen && can("service.external")],
    // § 16.8.2: a finished work is opened for editing by a separate right, corrected, closed.
    ["open", t("service.open_job"), finished && !job.editing_since && orderOpen && can("service.reopen")],
    ["correct", t("service.correct_job"), finished && Boolean(job.editing_since) && orderOpen && can("service.reopen")],
    ["close", t("service.close_job"), finished && Boolean(job.editing_since) && can("service.reopen")],
  ];
  const visible = buttons.filter(([, , shown]) => shown);
  if (visible.length === 0) return null;
  return (
    <>
      <div className="actions">
        {visible.map(([key, label]) =>
          key === "take" ? (
            <Take key={key} job={job} />
          ) : key === "close" ? (
            <CloseEditing key={key} job={job} />
          ) : (
            <button key={key} type="button" className="secondary small" onClick={() => setMode(mode === key ? "" : key)}>
              {label}
            </button>
          ),
        )}
      </div>
      {job.editing_since ? <p className="badge warn">{t("service.job_editing")}</p> : null}
      {mode ? <JobForm key={mode} mode={mode} job={job} zone={zone} vehicleCardId={op.vehicle_card_id} onDone={() => setMode("")} /> : null}
    </>
  );
}

function CloseEditing({ job }: { job: ServiceJob }) {
  const { t } = useTranslation();
  const company = useCompany();
  const refresh = useRefresh();
  const close = useCommand<Record<string, unknown>, Result>(`/c/${company.public_id}/service/jobs/${job.id}/close`);
  return (
    <>
      <button
        type="button"
        className="secondary small"
        disabled={close.pending}
        onClick={() => void close.run({ expected_version: job.version }).then((r) => (r ? refresh() : undefined))}
      >
        {t("service.close_job")}
      </button>
      <ErrorMessage error={close.error} />
    </>
  );
}

function Take({ job }: { job: ServiceJob }) {
  const { t } = useTranslation();
  const company = useCompany();
  const refresh = useRefresh();
  const take = useCommand<Record<string, unknown>, Result>(`/c/${company.public_id}/service/jobs/${job.id}/take`);
  return (
    <>
      <button
        type="button"
        className="secondary small"
        disabled={take.pending}
        onClick={() => void take.run({ expected_version: job.version }).then((r) => (r ? refresh() : undefined))}
      >
        {t("service.take")}
      </button>
      <ErrorMessage error={take.error} />
    </>
  );
}

function JobForm({
  mode,
  job,
  zone,
  vehicleCardId,
  onDone,
}: {
  mode: Exclude<Mode, "">;
  job: ServiceJob;
  zone: string;
  vehicleCardId: string;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const company = useCompany();
  const refresh = useRefresh();
  const executors = useExecutors(mode === "assign");
  // The values shown at the opening of the form: a correction sends only what was changed, so
  // that the seconds the form does not show and the odometer reading stay (§ 16.8.2).
  const [initial] = useState(() => ({
    at: toLocalInput(mode === "correct" && job.actual_end ? new Date(job.actual_end) : new Date(), zone),
    startAt: job.actual_start ? toLocalInput(new Date(job.actual_start), zone) : "",
    odometer: mode === "correct" && job.odometer ? String(Number(job.odometer)) : "",
    comment: mode === "correct" ? job.comment : "",
  }));
  const [at, setAt] = useState(initial.at);
  const [startAt, setStartAt] = useState(initial.startAt);
  const [odometer, setOdometer] = useState(initial.odometer);
  const [comment, setComment] = useState(initial.comment);
  const [reason, setReason] = useState("");
  const [amount, setAmount] = useState(job.price !== null ? String(Number(job.price)) : "");
  const [executor, setExecutor] = useState(job.executor_id ?? "");
  const [cost, setCost] = useState(job.contractor_cost !== null ? String(Number(job.contractor_cost)) : "");
  // § 16.5.4: the damages fixed by the work are chosen explicitly, with photos of the result.
  const [fixed, setFixed] = useState<FixedChoice[]>([]);
  const contractor = job.contractor_id !== null;
  // § 8.2.13: the system work «Odometer replacement» takes two readings instead of one.
  const replacement = job.system_work === "odometer_replacement";
  const [oldFinal, setOldFinal] = useState("");
  const [newStart, setNewStart] = useState("");
  const path = mode === "assign" ? "assign" : mode;
  const command = useCommand<Record<string, unknown>, Result>(`/c/${company.public_id}/service/jobs/${job.id}/${path}`);
  const number = (value: string) => value.trim().replace(",", ".") || null;

  async function submit(event: FormEvent) {
    event.preventDefault();
    const body: Record<string, unknown> = { expected_version: job.version };
    if (mode === "start") body.actual_start = fromLocalInput(at, zone);
    if (mode === "complete" || mode === "terminate") {
      body.actual_end = fromLocalInput(at, zone);
      body.comment = comment;
    }
    if (mode === "complete" && !replacement) body.odometer = number(odometer);
    if (mode === "complete" && replacement) {
      body.odometer_replacement = { old_final: number(oldFinal), new_start: number(newStart) };
    }
    if (mode === "complete" && fixed.length) body.fixed_damages = fixed;
    if (contractor && (mode === "complete" || mode === "terminate" || mode === "contractor-cost")) {
      body.contractor_cost = number(cost);
    }
    if (mode === "terminate" || mode === "cancel") body.reason = reason;
    if (mode === "terminate" || mode === "price") body.price = number(amount);
    if (mode === "assign") body.executor_id = executor || null;
    if (mode === "open") body.reason = reason;
    if (mode === "correct") {
      if (startAt && startAt !== initial.startAt) body.actual_start = fromLocalInput(startAt, zone);
      if (job.actual_end && at !== initial.at) body.actual_end = fromLocalInput(at, zone);
      if (job.state === "done" && odometer.trim() && odometer !== initial.odometer) body.odometer = number(odometer);
      if (comment !== initial.comment) body.comment = comment;
      body.reason = reason;
    }
    const result = await command.run(body);
    if (result) {
      await refresh();
      onDone();
    }
  }

  const fields: ReactNode[] = [];
  if (mode === "open") {
    fields.push(
      <p key="hint" className="muted small" style={{ flexBasis: "100%" }}>
        {t("service.open_job_hint")}
      </p>,
      <Field key="reason" label={t("service.reopen_reason")}>
        <input value={reason} onChange={(e) => setReason(e.target.value)} />
      </Field>,
    );
  }
  if (mode === "correct") {
    fields.push(
      <p key="hint" className="muted small" style={{ flexBasis: "100%" }}>
        {t("service.correct_hint")}
      </p>,
      <Field key="start" label={t("service.actual_start")}>
        <input type="datetime-local" value={startAt} onChange={(e) => setStartAt(e.target.value)} />
      </Field>,
      <Field key="end" label={t("service.actual_end")}>
        <input type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} />
      </Field>,
    );
    if (job.state === "done") {
      fields.push(
        <Field key="odometer" label={t("service.odometer")}>
          <input inputMode="decimal" value={odometer} onChange={(e) => setOdometer(e.target.value)} />
        </Field>,
      );
    }
    fields.push(
      <Field key="comment" label={t("service.comment")}>
        <input value={comment} onChange={(e) => setComment(e.target.value)} />
      </Field>,
      <Field key="reason" label={t("service.correct_reason")}>
        <input value={reason} onChange={(e) => setReason(e.target.value)} />
      </Field>,
    );
  }
  if (mode === "start" || mode === "complete" || mode === "terminate") {
    fields.push(
      <Field key="at" label={mode === "start" ? t("service.actual_start") : t("service.actual_end")}>
        <input type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} />
      </Field>,
    );
  }
  if (contractor && (mode === "complete" || mode === "terminate" || mode === "contractor-cost")) {
    fields.push(
      <Field key="cost" label={`${t("service.contractor_cost")}, ${job.currency}`} hint={t("service.contractor_cost_hint")}>
        <input inputMode="decimal" value={cost} onChange={(e) => setCost(e.target.value)} />
      </Field>,
    );
  }
  if (mode === "complete") {
    fields.push(<FixedDamagesPicker key="fixed" vehicleCardId={vehicleCardId} value={fixed} onChange={setFixed} />);
    if (replacement) {
      fields.push(
        <OdometerReplacementFields
          key="replacement"
          vehicleCardId={vehicleCardId}
          at={at ? fromLocalInput(at, zone) : ""}
          oldFinal={oldFinal}
          newStart={newStart}
          onOldFinal={setOldFinal}
          onNewStart={setNewStart}
        />,
      );
    } else {
      fields.push(
        <Field key="odometer" label={t("service.odometer")} hint={contractor ? t("service.odometer_optional") : undefined}>
          <input inputMode="decimal" value={odometer} onChange={(e) => setOdometer(e.target.value)} />
        </Field>,
      );
    }
  }
  if (mode === "terminate" || mode === "cancel") {
    fields.push(
      <Field key="reason" label={t("service.end_reason")}>
        <input value={reason} onChange={(e) => setReason(e.target.value)} />
      </Field>,
    );
  }
  if (mode === "terminate" || mode === "price") {
    fields.push(
      <Field key="price" label={`${t("service.price")}, ${job.currency}`} hint={t("service.zero_hint")}>
        <input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />
      </Field>,
    );
  }
  if (mode === "complete" || mode === "terminate") {
    fields.push(
      <Field key="comment" label={t("service.comment")}>
        <input value={comment} onChange={(e) => setComment(e.target.value)} />
      </Field>,
    );
  }
  if (mode === "assign") {
    fields.push(
      <Field key="executor" label={t("service.executor")}>
        <select value={executor} onChange={(e) => setExecutor(e.target.value)}>
          <option value="">{t("service.no_executor")}</option>
          {(executors.data?.employees ?? []).map((e) => (
            <option key={e.id} value={e.id}>
              {e.name}
            </option>
          ))}
        </select>
      </Field>,
    );
  }
  const blocked =
    (mode === "complete" && !replacement && !contractor && !odometer.trim()) ||
    (mode === "complete" && replacement && (!oldFinal.trim() || !newStart.trim())) ||
    (mode === "contractor-cost" && !cost.trim()) ||
    (mode === "terminate" && !reason.trim()) ||
    (mode === "correct" && !reason.trim()) ||
    (mode === "open" && !reason.trim()) ||
    (mode === "price" && !amount.trim());
  return (
    <form className="row" onSubmit={submit}>
      {fields}
      <div className="actions">
        <button type="submit" disabled={command.pending || blocked}>
          {t("common.save")}
        </button>
      </div>
      <ErrorMessage error={command.error} />
    </form>
  );
}

/** § 8.2.13: the last reading of the old odometer and the first of the new one; before saving the
 * resulting accumulated mileage is shown. */
function OdometerReplacementFields({
  vehicleCardId,
  at,
  oldFinal,
  newStart,
  onOldFinal,
  onNewStart,
}: {
  vehicleCardId: string;
  at: string;
  oldFinal: string;
  newStart: string;
  onOldFinal: (value: string) => void;
  onNewStart: (value: string) => void;
}) {
  const { t } = useTranslation();
  const company = useCompany();
  const clean = (value: string) => value.trim().replace(",", ".");
  const ready = Boolean(at && clean(oldFinal) && clean(newStart));
  const query = new URLSearchParams({ at, old_final: clean(oldFinal), new_start: clean(newStart) });
  const preview = useApi<{ preview: { accumulated: string; unit: string; conflicts: { measured_at: string; value: string }[] } }>(
    ["odometer-replacement", company.public_id, vehicleCardId, query.toString()],
    `/c/${company.public_id}/vehicles/${vehicleCardId}/odometer/replacement-preview?${query.toString()}`,
    ready,
  );
  const result = preview.data?.preview;
  return (
    <>
      <Field label={t("vehicles.replacement_old_final")}>
        <input inputMode="decimal" value={oldFinal} onChange={(e) => onOldFinal(e.target.value)} />
      </Field>
      <Field label={t("vehicles.replacement_new_start")}>
        <input inputMode="decimal" value={newStart} onChange={(e) => onNewStart(e.target.value)} />
      </Field>
      <p className="small" style={{ flexBasis: "100%" }}>
        {result ? (
          <>
            {t("vehicles.replacement_accumulated", { value: Number(result.accumulated), unit: t(`vehicles.unit_${result.unit}`) })}
            {result.conflicts.length ? (
              <span className="badge warn" style={{ marginLeft: 8 }}>
                {t("vehicles.replacement_conflicts", { values: result.conflicts.map((c) => Number(c.value)).join(", ") })}
              </span>
            ) : null}
          </>
        ) : (
          <span className="muted">{t("vehicles.replacement_hint")}</span>
        )}
      </p>
    </>
  );
}

/** Parts of a work: issued, returned unused, consumed; «done» consumes what is left (§ 16.5.1).
 * Everything is consumed or returned before the order closes (§ 16.7.3). */
function Materials({ op, job }: { op: ServiceOperation; job: ServiceJob }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const refresh = useRefresh();
  const [adding, setAdding] = useState(false);
  const items = op.materials.filter((m) => m.job_id === job.id);
  const open = job.state === "planned" || job.state === "in_progress";
  const mayAdd = open && !FINAL_STATES.includes(op.state) && can("service.create") && can("warehouse.issue");
  if (items.length === 0 && !mayAdd) return null;
  return (
    <div className="small" style={{ marginTop: 8 }}>
      {items.map((m) => (
        <MaterialRow key={m.id} material={m} />
      ))}
      {mayAdd ? (
        adding ? (
          <StockAdder
            path={`/c/${company.public_id}/service/jobs/${job.id}/materials`}
            title={t("service.add_material")}
            button={t("service.issue_material")}
            extra={{ expected_version: job.version }}
            onAdded={async (result) => {
              if (result !== null) {
                setAdding(false);
                await refresh();
              }
            }}
          />
        ) : (
          <button type="button" className="secondary small" onClick={() => setAdding(true)}>
            {t("service.add_material")}
          </button>
        )
      ) : null}
    </div>
  );
}

function MaterialRow({ material }: { material: ServiceMaterial }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const refresh = useRefresh();
  const [mode, setMode] = useState<"" | "return" | "consume" | "price">("");
  const [value, setValue] = useState("");
  const command = useCommand<Record<string, unknown>, Result>(
    `/c/${company.public_id}/service/materials/${material.id}/${mode || "return"}`,
  );
  const outstanding = Number(material.outstanding) > 0;
  const unit = t(`warehouse.unit_short_${material.unit}`);
  async function submit(event: FormEvent) {
    event.preventDefault();
    const amount = value.trim().replace(",", ".");
    const body: Record<string, unknown> = { expected_version: material.version };
    if (mode === "price") body.price = amount;
    else if (amount) body.quantity = amount;
    const result = await command.run(body);
    if (result) {
      setMode("");
      setValue("");
      await refresh();
    }
  }
  return (
    <div>
      {material.item_name} {material.label ? <span className="muted">{material.label}</span> : null} ·{" "}
      {t("service.material_state", {
        quantity: Number(material.quantity),
        consumed: Number(material.consumed),
        returned: Number(material.returned),
        unit,
      })}{" "}
      · {material.price === null ? t("service.price_unknown") : `${Number(material.price)} ${material.currency}`}
      {Number(material.price_per) !== 1 ? ` / ${Number(material.price_per)} ${unit}` : ""}
      {outstanding ? <span className="badge warn">{t("service.material_outstanding", { quantity: Number(material.outstanding), unit })}</span> : null}{" "}
      {outstanding && can("warehouse.issue") ? (
        <button type="button" className="link" onClick={() => setMode(mode === "return" ? "" : "return")}>
          {t("service.return_material")}
        </button>
      ) : null}{" "}
      {outstanding && can("service.work") ? (
        <button type="button" className="link" onClick={() => setMode(mode === "consume" ? "" : "consume")}>
          {t("service.consume_material")}
        </button>
      ) : null}{" "}
      {can("service.price") ? (
        <button type="button" className="link" onClick={() => setMode(mode === "price" ? "" : "price")}>
          {t("service.change_price")}
        </button>
      ) : null}
      {mode ? (
        <form className="row" onSubmit={submit}>
          <Field
            label={mode === "price" ? `${t("service.price")}, ${material.currency}` : t("service.quantity_unit", { unit })}
            hint={mode === "price" ? t("service.zero_hint") : t("service.all_outstanding")}
          >
            <input inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)} />
          </Field>
          <div className="actions">
            <button type="submit" disabled={command.pending || (mode === "price" && !value.trim())}>
              {t("common.save")}
            </button>
          </div>
          <ErrorMessage error={command.error} />
        </form>
      ) : null}
    </div>
  );
}

/** The vehicle or a part handed over to a contractor and returned, apart from the results of
 * the works (§ 16.6.6). */
function Handovers({ op, zone }: { op: ServiceOperation; zone: string }) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const refresh = useRefresh();
  const contractors = useContractors(can("service.handover"));
  const [kind, setKind] = useState<"vehicle" | "part">("vehicle");
  const [description, setDescription] = useState("");
  const [contractorId, setContractorId] = useState(op.counterparty_id ?? "");
  const [at, setAt] = useState(toLocalInput(new Date(), zone));
  const hand = useCommand<Record<string, unknown>, Result>(opCommand(company.public_id, op, "handovers"));
  const mayHand = can("service.handover") && !FINAL_STATES.includes(op.state);
  if (op.handovers.length === 0 && !mayHand) return null;
  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await hand.run({
      kind,
      description,
      counterparty_id: contractorId || null,
      handed_at: fromLocalInput(at, zone),
      expected_version: op.version,
    });
    if (result) {
      setDescription("");
      await refresh();
    }
  }
  return (
    <Card title={t("service.handovers")}>
      {op.handovers.map((h) => (
        <div key={h.id} className="small">
          {h.kind === "vehicle" ? t("service.handover_vehicle") : h.description} → {h.counterparty_name ?? "—"} ·{" "}
          {formatMoment(h.handed_at, zone, i18n.language)}
          {h.returned_at
            ? ` · ${t("service.returned_at", { at: formatMoment(h.returned_at, zone, i18n.language) })}`
            : null}
          {!h.returned_at && can("service.handover") ? <ReturnHandover handoverId={h.id} version={h.version} zone={zone} /> : null}
        </div>
      ))}
      {mayHand ? (
        <form className="row" onSubmit={submit}>
          <Field label={t("service.handover_what")}>
            <select value={kind} onChange={(e) => setKind(e.target.value as "vehicle" | "part")}>
              <option value="vehicle">{t("service.handover_vehicle")}</option>
              <option value="part">{t("service.handover_part")}</option>
            </select>
          </Field>
          {kind === "part" ? (
            <Field label={t("service.part_description")}>
              <input value={description} onChange={(e) => setDescription(e.target.value)} />
            </Field>
          ) : null}
          <Field label={t("service.contractor")}>
            <select value={contractorId} onChange={(e) => setContractorId(e.target.value)}>
              <option value="">—</option>
              {(contractors.data?.counterparties ?? []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("service.handed_at")}>
            <input type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} />
          </Field>
          <div className="actions">
            <button type="submit" className="secondary" disabled={hand.pending || !contractorId || (kind === "part" && !description.trim())}>
              {t("service.hand_over")}
            </button>
          </div>
          <ErrorMessage error={hand.error} />
        </form>
      ) : null}
    </Card>
  );
}

function ReturnHandover({ handoverId, version, zone }: { handoverId: string; version: number; zone: string }) {
  const { t } = useTranslation();
  const company = useCompany();
  const refresh = useRefresh();
  const [at, setAt] = useState(toLocalInput(new Date(), zone));
  const back = useCommand<Record<string, unknown>, Result>(`/c/${company.public_id}/service/handovers/${handoverId}/return`);
  return (
    <form
      className="row"
      onSubmit={(event) => {
        event.preventDefault();
        void back.run({ returned_at: fromLocalInput(at, zone), expected_version: version }).then((r) => (r ? refresh() : undefined));
      }}
    >
      <Field label={t("service.returned")}>
        <input type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} />
      </Field>
      <div className="actions">
        <button type="submit" className="secondary small" disabled={back.pending}>
          {t("service.mark_returned")}
        </button>
      </div>
      <ErrorMessage error={back.error} />
    </form>
  );
}
