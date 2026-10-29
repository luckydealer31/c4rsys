import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router";

import { ApiError } from "../../api/client";
import type { ServiceJob, ServiceOperation } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { formatMoment, fromLocalInput, toLocalInput } from "../../time";
import { useLocations } from "../settings/LocationsPage";
import {
  FINAL_STATES,
  HOLDING_STATES,
  OPERATION_KEYS,
  price,
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
          <dt>{t("service.location")}</dt>
          <dd>{location?.name ?? "—"}</dd>
          <dt>{t("service.reason")}</dt>
          <dd>{op.reason}</dd>
          <dt>{t("service.plan")}</dt>
          <dd>{op.planned_start ? `${when(op.planned_start)} — ${when(op.planned_end)}` : t("service.no_plan")}</dd>
          <dt>{t("service.place")}</dt>
          <dd>{op.post_name ?? t("service.general_zone")}</dd>
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
      <Jobs op={op} zone={zone} />
      {!final ? <OperationActions op={op} zone={zone} /> : null}
    </div>
  );
}

function OperationActions({ op, zone }: { op: ServiceOperation; zone: string }) {
  const can = useCan();
  const holding = HOLDING_STATES.includes(op.state);
  return (
    <>
      {can("service.create") ? <AddJob op={op} /> : null}
      {op.state === "planned" && can("service.accept") ? <Accept op={op} zone={zone} /> : null}
      {(op.state === "planned" || op.state === "accepted") && can("service.create") ? <Plan op={op} zone={zone} /> : null}
      {holding && can("service.place") ? <Place op={op} /> : null}
      {holding && can("service.complete") ? <Finish op={op} /> : null}
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
  const works = useServiceWorks();
  const [workId, setWorkId] = useState("");
  const add = useCommand<Record<string, unknown>, Result>(opCommand(company.public_id, op, "jobs"));
  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await add.run({ work_id: workId, expected_version: op.version });
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
            {job.executor_name ?? t("service.no_executor")}
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
          <JobActions op={op} job={job} zone={zone} />
        </div>
      ))}
    </Card>
  );
}

type Mode = "" | "start" | "complete" | "terminate" | "cancel" | "price" | "assign";

function JobActions({ op, job, zone }: { op: ServiceOperation; job: ServiceJob; zone: string }) {
  const { t } = useTranslation();
  const can = useCan();
  const [mode, setMode] = useState<Mode>("");
  const holding = HOLDING_STATES.includes(op.state);
  const orderOpen = op.order_state !== "closed" && op.order_state !== "cancelled";
  const buttons: [Mode | "take", string, boolean][] = [
    ["take", t("service.take"), job.state === "planned" && !job.executor_id && can("service.work")],
    ["assign", t("service.assign"), (job.state === "planned" || job.state === "in_progress") && can("service.assign")],
    ["start", t("service.start"), job.state === "planned" && holding && can("service.work")],
    ["complete", t("service.complete"), job.state === "in_progress" && can("service.work")],
    ["terminate", t("service.terminate_job"), job.state === "in_progress" && can("service.work")],
    ["cancel", t("service.cancel_job"), job.state === "planned" && can("service.cancel")],
    ["price", t("service.change_price"), job.state !== "cancelled" && orderOpen && can("service.price")],
  ];
  const visible = buttons.filter(([, , shown]) => shown);
  if (visible.length === 0) return null;
  return (
    <>
      <div className="actions">
        {visible.map(([key, label]) =>
          key === "take" ? (
            <Take key={key} job={job} />
          ) : (
            <button key={key} type="button" className="secondary small" onClick={() => setMode(mode === key ? "" : key)}>
              {label}
            </button>
          ),
        )}
      </div>
      {mode ? <JobForm key={mode} mode={mode} job={job} zone={zone} onDone={() => setMode("")} /> : null}
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

function JobForm({ mode, job, zone, onDone }: { mode: Exclude<Mode, "">; job: ServiceJob; zone: string; onDone: () => void }) {
  const { t } = useTranslation();
  const company = useCompany();
  const refresh = useRefresh();
  const executors = useExecutors(mode === "assign");
  const [at, setAt] = useState(toLocalInput(new Date(), zone));
  const [odometer, setOdometer] = useState("");
  const [comment, setComment] = useState("");
  const [reason, setReason] = useState("");
  const [amount, setAmount] = useState(job.price !== null ? String(Number(job.price)) : "");
  const [executor, setExecutor] = useState(job.executor_id ?? "");
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
    if (mode === "complete") body.odometer = number(odometer);
    if (mode === "terminate" || mode === "cancel") body.reason = reason;
    if (mode === "terminate" || mode === "price") body.price = number(amount);
    if (mode === "assign") body.executor_id = executor || null;
    const result = await command.run(body);
    if (result) {
      await refresh();
      onDone();
    }
  }

  const fields: ReactNode[] = [];
  if (mode === "start" || mode === "complete" || mode === "terminate") {
    fields.push(
      <Field key="at" label={mode === "start" ? t("service.actual_start") : t("service.actual_end")}>
        <input type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} />
      </Field>,
    );
  }
  if (mode === "complete") {
    fields.push(
      <Field key="odometer" label={t("service.odometer")}>
        <input inputMode="decimal" value={odometer} onChange={(e) => setOdometer(e.target.value)} />
      </Field>,
    );
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
    (mode === "complete" && !odometer.trim()) ||
    (mode === "terminate" && !reason.trim()) ||
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
