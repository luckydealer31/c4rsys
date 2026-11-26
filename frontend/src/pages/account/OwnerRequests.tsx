import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../../api/client";
import type { IncomingOwnerRequest } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Info, Loading, Modal } from "../../components/ui";
import { DocumentChecklist } from "../vehicles/VehicleDocuments";

const QUESTIONS = ["link", "ownership", "management"] as const;

/** Requests of companies to the owner of a vehicle (§ 8.3.1). An open request is a short card
 * with «Answer»: the questions come in windows one after another, each alone (customer's remark
 * of 25.11.2026); an answered request stays as its result. `base` is `/owner-requests` for a
 * person or `/c/<CMP>/incoming-owner-requests` for a company. */
export function OwnerRequests({ base, title, hideEmpty }: { base: string; title: string; hideEmpty?: boolean }) {
  const { t } = useTranslation();
  const requests = useQuery({
    queryKey: ["incoming-owner-requests", base],
    queryFn: async () => (await api<{ requests: IncomingOwnerRequest[] }>(base)).requests,
  });
  if (requests.isLoading) return <Loading />;
  const open = (requests.data ?? []).filter((r) => r.state === "open");
  const answered = (requests.data ?? []).filter((r) => r.state === "complete");
  if (!open.length && !answered.length && hideEmpty) return null;
  return (
    <>
      <h2>{title}</h2>
      {!open.length && !answered.length ? <p className="muted">{t("links.none_incoming")}</p> : null}
      {open.map((r) => (
        <OwnerRequestOpen key={r.id} base={base} request={r} />
      ))}
      {answered.map((r) => (
        <OwnerRequestResult key={r.id} base={base} request={r} />
      ))}
    </>
  );
}

type Question = (typeof QUESTIONS)[number];
const PUBLIC = ["legal_name", "legal_form", "identifiers", "legal_address", "actual_address", "phone", "email", "website"] as const;

function vehicleName(request: IncomingOwnerRequest) {
  const v = request.vehicle;
  return [v.make, v.model, v.year].filter(Boolean).join(" ") || v.vehicle_id;
}

function VehicleLine({ request }: { request: IncomingOwnerRequest }) {
  const v = request.vehicle;
  return (
    <p>
      <strong>{vehicleName(request)}</strong>
      {v.plate ? ` · ${v.plate}` : ""}
      {v.vin ? <span className="muted small"> · VIN {v.vin}</span> : null}
    </p>
  );
}

/** An open request: who asks about which vehicle, and «Answer». */
function OwnerRequestOpen({ base, request }: { base: string; request: IncomingOwnerRequest }) {
  const { t } = useTranslation();
  const [answering, setAnswering] = useState(false);
  return (
    <Card title={request.company.display_name}>
      <VehicleLine request={request} />
      <div className="row-actions" style={{ justifyContent: "flex-start" }}>
        <button type="button" className="small-button" onClick={() => setAnswering(true)}>
          {t("owner_requests.answer")}
        </button>
      </div>
      {answering ? <OwnerRequestSteps base={base} request={request} onClose={() => setAnswering(false)} /> : null}
    </Card>
  );
}

/** An answered request: what was answered. */
function OwnerRequestResult({ base, request }: { base: string; request: IncomingOwnerRequest }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const short = { link: "result_link", ownership: "result_ownership", management: "result_management" } as const;
  return (
    <Card title={request.company.display_name}>
      <VehicleLine request={request} />
      <dl className="facts">
        {QUESTIONS.filter((q) => request.answers[q] !== undefined).map((q) => (
          <div key={q} style={{ display: "contents" }}>
            <dt>{t(`owner_requests.${short[q]}`)}</dt>
            <dd>
              <span className={`badge ${request.answers[q] ? "ok" : "warn"}`}>
                {request.answers[q] ? t("owner_requests.yes") : t("owner_requests.no")}
              </span>
            </dd>
          </div>
        ))}
      </dl>
      {/* The owner — a person or a company — takes the consent back here (26.11.2026). */}
      {request.consent_active ? (
        <WithdrawConsent
          path={`${base}/${request.id}/withdraw`}
          company={request.company.display_name}
          onDone={async () => {
            await client.invalidateQueries({ queryKey: ["incoming-owner-requests", base] });
          }}
        />
      ) : null}
    </Card>
  );
}

/** The owner takes back his consent to the management by one company (customer's decision of
 * 26.11.2026): a button; then what he is to know — the rentals that last and the bookings that
 * wait — a comment and «Confirm». The consent is taken back at once. */
export function WithdrawConsent({
  path,
  company,
  onDone,
}: {
  path: string;
  company: string;
  onDone: () => Promise<void>;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [comment, setComment] = useState("");
  const load = useQuery({
    queryKey: ["withdraw-load", path],
    queryFn: () => api<{ active_rentals: number; bookings: number }>(path),
    enabled: open,
    retry: false,
  });
  const withdraw = useCommand<{ comment: string }, unknown>(path);
  if (!open) {
    return (
      <div className="actions">
        <button type="button" className="secondary small" data-testid="withdraw-consent" onClick={() => setOpen(true)}>
          {t("cabinet.withdraw_start")}
        </button>
      </div>
    );
  }
  const busy = (load.data?.active_rentals ?? 0) + (load.data?.bookings ?? 0) > 0;
  return (
    <div className="stack" data-testid="withdraw-form" style={{ maxWidth: 460 }}>
      {load.isLoading ? <Loading /> : null}
      {load.data ? (
        <p className={busy ? "alert warn small" : "muted small"}>
          {t("cabinet.withdraw_load", { rentals: load.data.active_rentals, bookings: load.data.bookings })}
        </p>
      ) : null}
      <p className="small">{t("cabinet.withdraw_warning", { company })}</p>
      <Field label={t("cabinet.withdraw_comment")}>
        <input value={comment} onChange={(e) => setComment(e.target.value)} />
      </Field>
      <ErrorMessage error={withdraw.error} />
      <div className="actions">
        <button
          type="button"
          className="small"
          disabled={withdraw.pending || !load.data}
          onClick={async () => {
            if ((await withdraw.run({ comment: comment.trim() })) !== null) {
              setOpen(false);
              await onDone();
            }
          }}
        >
          {t("links.break_yes")}
        </button>
        <button type="button" className="secondary small" onClick={() => setOpen(false)}>
          {t("common.cancel")}
        </button>
      </div>
    </div>
  );
}

/** The questions in a window, one at a time: the link with the company (not asked when it is
 * there already), the vehicle, the management. «No» to the link ends the questions; «no» to
 * the ownership makes the management «no»; the answers go together after the last one. */
function OwnerRequestSteps({
  base,
  request,
  onClose,
}: {
  base: string;
  request: IncomingOwnerRequest;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [choice, setChoice] = useState<Partial<Record<Question, boolean>>>({});
  const [passed, setPassed] = useState<string[]>([]);
  const reply = useCommand<{ answers: Partial<Record<Question, boolean>>; document_ids?: string[] }, unknown>(
    `${base}/${request.id}/answer`,
  );
  const company = request.company.display_name;
  const vehicle = vehicleName(request);
  const known: Partial<Record<Question, boolean>> = {
    ...(request.linked ? { link: true } : {}),
    ...(request.owned ? { ownership: true } : {}),
    ...request.answers,
    ...choice,
  };
  const stop = known.link === false || known.ownership === false;
  const next = stop ? null : (QUESTIONS.find((q) => known[q] === undefined) ?? null);
  const confirms = QUESTIONS.every((q) => known[q] === true);
  // Copies of documents go with the answer that makes all three «yes» (decision of 12.11.2026).
  const documentsStep = next === null && confirms && request.documents.length > 0;

  async function send(answers: Partial<Record<Question, boolean>>, documents: string[]) {
    const body = documents.length ? { answers, document_ids: documents } : { answers };
    if ((await reply.run(body)) !== null) {
      await client.invalidateQueries({ queryKey: ["incoming-owner-requests", base] });
      onClose();
    }
  }

  function choose(q: Question, value: boolean) {
    const made = { ...choice, [q]: value };
    setChoice(made);
    const after = { ...known, [q]: value };
    const ended = after.link === false || after.ownership === false || QUESTIONS.every((x) => after[x] !== undefined);
    const withDocuments = QUESTIONS.every((x) => after[x] === true) && request.documents.length > 0;
    if (ended && !withDocuments) void send(made, []);
  }

  const facts = request.company.public ?? {};
  const value = (key: (typeof PUBLIC)[number]) => {
    const raw = facts[key];
    if (!raw) return "";
    if (Array.isArray(raw)) return raw.map((item) => [item.type, item.value].filter(Boolean).join(": ")).join("; ");
    return String(raw);
  };
  const buttons = (q: Question) => (
    <div className="row-actions" style={{ justifyContent: "flex-start", marginTop: 12 }}>
      <button type="button" className="small-button" disabled={reply.pending} onClick={() => choose(q, true)}>
        {t("owner_requests.yes")}
      </button>
      <button type="button" className="secondary small-button" disabled={reply.pending} onClick={() => choose(q, false)}>
        {t("owner_requests.no")}
      </button>
    </div>
  );
  // The numbers of the vehicle under the title that names it.
  const numbers = [request.vehicle.plate, request.vehicle.vin ? `VIN ${request.vehicle.vin}` : ""].filter(Boolean).join(" · ");
  const hint = (q: Question) =>
    [q === "management" ? "" : t(`owner_requests.step_${q}_hint`), t("owner_requests.final")].filter(Boolean).join(" ");

  return (
    <Modal title={next === "link" ? company : vehicle} onClose={onClose}>
      {next === "link" ? (
        <>
          {PUBLIC.some((key) => value(key)) ? (
            <dl className="facts small">
              {PUBLIC.filter((key) => value(key)).map((key) => (
                <div key={key} style={{ display: "contents" }}>
                  <dt>{t(`company.${key}`)}</dt>
                  <dd>{value(key)}</dd>
                </div>
              ))}
            </dl>
          ) : null}
          <p style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <strong>{t("owner_requests.step_link", { company })}</strong>
            <Info text={hint("link")} />
          </p>
          {buttons("link")}
        </>
      ) : null}
      {next === "ownership" ? (
        <>
          {numbers ? <p className="muted small">{numbers}</p> : null}
          <p style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <strong>{t("owner_requests.step_ownership", { vehicle })}</strong>
            <Info text={hint("ownership")} />
          </p>
          {buttons("ownership")}
        </>
      ) : null}
      {next === "management" ? (
        <>
          {numbers ? <p className="muted small">{numbers}</p> : null}
          <p style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <strong>{t("owner_requests.step_management", { company })}</strong>
            <Info text={hint("management")} />
          </p>
          {buttons("management")}
        </>
      ) : null}
      {documentsStep ? (
        <>
          <h3>{t("owner_requests.documents_title")}</h3>
          <p className="muted small">{t("owner_requests.documents_hint")}</p>
          <DocumentChecklist items={request.documents} chosen={passed} onChange={setPassed} />
          <div className="actions">
            <button type="button" disabled={reply.pending} onClick={() => void send(choice, passed)}>
              {t("owner_requests.answer")}
            </button>
          </div>
        </>
      ) : null}
      <ErrorMessage error={reply.error} />
    </Modal>
  );
}
