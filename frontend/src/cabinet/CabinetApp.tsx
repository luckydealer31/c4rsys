import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, Navigate, Route, Routes, useParams } from "react-router";

import { api } from "../api/client";
import { useApi } from "../api/queries";
import type { User } from "../api/types";
import { useCommand } from "../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../components/ui";
import { RailLink } from "../layout/AppShell";
import { UserMenu } from "../layout/UserMenu";
import { LinkRequests } from "../pages/account/LinkRequests";
import { OwnerRequests } from "../pages/account/OwnerRequests";
import { ProfilePage } from "../pages/account/ProfilePage";
import { deviceTimeZone, formatMoment } from "../time";

/** Whether this address serves the personal cabinet (customer's decision of 30.10.2026: its
 * own domain with its own sign-in; the B2B domain keeps the company interface). */
export function useSite() {
  return useQuery({
    queryKey: ["site"],
    queryFn: () => api<{ cabinet: boolean }>("/site"),
    staleTime: Infinity,
    retry: false,
  });
}

type LinkedCompany = {
  link_id: string;
  company: { public_id: string; display_name: string };
  card_type: "client" | "counterparty";
  state: "accepted" | "broken";
  linked_at: string | null;
  broken_at: string | null;
};

type Operation = {
  type: "rental" | "extra_service" | "service" | "sale";
  id: string;
  state: string;
  vehicle?: string;
  title?: string;
  start?: string | null;
  end?: string | null;
  issue_place?: string;
  return_place?: string;
  quantity?: string | number | null;
  amount?: string;
  currency?: string;
  works?: { name: string; state: string }[];
  materials?: { name: string; quantity: string; unit: string }[];
};

type HistoryOrder = {
  id: string;
  number: number;
  state: string;
  created_at: string;
  closed_at: string | null;
  operations: Operation[];
  lines: { source: string; name: string; currency: string; amount: string }[];
  amounts: Record<string, { charged: string; paid: string; to_pay: string; overpaid: string }>;
};

type CabinetDocument = {
  id: string;
  company: { public_id: string; display_name: string };
  number: number;
  title: string;
  kind: string;
  kind_code: string;
  document_date: string;
  version_number: number | null;
  signer: { id: string; state: string; name: string; decided_at: string | null } | null;
  content?: string;
  has_file?: boolean;
  file_id?: string | null;
  file_name?: string;
};

function useWhen() {
  const { i18n } = useTranslation();
  return (value: string | null | undefined) => formatMoment(value, deviceTimeZone(), i18n.language);
}

/** The frame of the personal cabinet: its own sections, no companies' interface (§ 7.1.2). */
function CabinetShell({ user, children }: { user: User; children: ReactNode }) {
  const { t } = useTranslation();
  return (
    <div className="shell">
      <header className="topbar">
        <Link to="/" className="brand">
          c4rsys · {t("cabinet.title")}
        </Link>
        <span className="spacer" />
        <UserMenu user={user} />
      </header>
      <div className="shell-body">
        <aside className="sidebar">
          <nav className="rail">
            <RailLink to="/" icon="companies" label={t("cabinet.companies")} />
            <RailLink to="/documents" icon="documents" label={t("cabinet.documents")} />
            <RailLink to="/garage" icon="garage" label={t("cabinet.garage")} />
            <RailLink to="/requests" icon="invitations" label={t("cabinet.requests")} />
            <RailLink to="/profile" icon="user" label={t("cabinet.profile")} />
          </nav>
        </aside>
        <main className="shell-main">{children}</main>
      </div>
    </div>
  );
}

export function CabinetApp({ user }: { user: User }) {
  const page = (node: ReactNode) => <CabinetShell user={user}>{node}</CabinetShell>;
  return (
    <Routes>
      <Route path="/" element={page(<CabinetHome />)} />
      <Route path="/companies/:linkId" element={page(<CompanyHistory />)} />
      <Route path="/documents" element={page(<DocumentsList />)} />
      <Route path="/documents/:id" element={page(<DocumentView />)} />
      <Route path="/garage" element={page(<Garage />)} />
      <Route path="/requests" element={page(<Requests />)} />
      <Route path="/profile" element={page(<ProfilePage user={user} />)} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

function CabinetHome() {
  const { t } = useTranslation();
  const when = useWhen();
  const companies = useApi<{ companies: LinkedCompany[] }>(["cabinet-companies"], "/cabinet/companies");
  return (
    <div className="page">
      <h1>{t("cabinet.companies")}</h1>
      <LinkRequests base="/profile-links" title={t("links.my_title")} hideEmpty pendingOnly />
      <OwnerRequests base="/owner-requests" title={t("owner_requests.my_title")} hideEmpty />
      <Card>
        {companies.isLoading ? <Loading /> : null}
        {companies.data?.companies.length === 0 ? <p className="muted">{t("cabinet.no_companies")}</p> : null}
        {(companies.data?.companies ?? []).map((c) => (
          <Link key={c.link_id} className="list-link" to={`/companies/${c.link_id}`}>
            <span>
              <strong>{c.company.display_name}</strong>{" "}
              <span className="muted small">{c.company.public_id}</span>
              <br />
              <span className="muted small">
                {c.state === "broken"
                  ? t("cabinet.link_broken", { at: when(c.broken_at) })
                  : t("cabinet.linked_since", { at: when(c.linked_at) })}
              </span>
            </span>
          </Link>
        ))}
      </Card>
    </div>
  );
}

function OperationView({ op }: { op: Operation }) {
  const { t } = useTranslation();
  const when = useWhen();
  const period = [op.start ? when(op.start) : "", op.end ? when(op.end) : ""].filter(Boolean).join(" — ");
  return (
    <div style={{ marginBottom: 8 }}>
      <strong>{t(`cabinet.op_${op.type}`)}</strong>
      {op.vehicle ? `: ${op.vehicle}` : op.title ? `: ${op.title}` : ""}{" "}
      <span className="muted small">{t(`cabinet.state_${op.state}`, { defaultValue: op.state })}</span>
      {period ? <div className="small muted">{period}</div> : null}
      {op.issue_place || op.return_place ? (
        <div className="small muted">
          {t("cabinet.places", { from: op.issue_place || "—", to: op.return_place || "—" })}
        </div>
      ) : null}
      {op.type === "sale" ? (
        <div className="small">
          {op.quantity} · {op.amount} {op.currency}
        </div>
      ) : null}
      {op.works?.length ? (
        <ul className="small">
          {op.works.map((w, i) => (
            <li key={i}>{w.name}</li>
          ))}
        </ul>
      ) : null}
      {op.materials?.length ? (
        <ul className="small muted">
          {op.materials.map((m, i) => (
            <li key={i}>
              {m.name} — {m.quantity} {m.unit}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function CompanyHistory() {
  const { t } = useTranslation();
  const when = useWhen();
  const { linkId = "" } = useParams();
  const companies = useApi<{ companies: LinkedCompany[] }>(["cabinet-companies"], "/cabinet/companies");
  const history = useApi<{ orders: HistoryOrder[] }>(
    ["cabinet-history", linkId],
    `/cabinet/companies/${linkId}/history`,
  );
  const company = companies.data?.companies.find((c) => c.link_id === linkId);
  return (
    <div className="page">
      <h1>{company?.company.display_name ?? ""}</h1>
      {company?.state === "broken" ? <p className="alert warn">{t("cabinet.broken_hint")}</p> : null}
      {history.isLoading ? <Loading /> : null}
      <ErrorMessage error={history.error as never} />
      {history.data?.orders.length === 0 ? <p className="muted">{t("cabinet.no_history")}</p> : null}
      {(history.data?.orders ?? []).map((o) => (
        <Card key={o.id} title={t("cabinet.order", { number: o.number })}>
          <p className="muted small">
            {when(o.created_at)} · {t(`orders.state_${o.state}`, { defaultValue: o.state })}
          </p>
          {o.operations.map((op) => (
            <OperationView key={op.id} op={op} />
          ))}
          {o.lines.length ? (
            <table className="table">
              <tbody>
                {o.lines.map((line, i) => (
                  <tr key={i}>
                    <td>
                      {t(`cabinet.source_${line.source}`)}
                      {line.name ? `: ${line.name}` : ""}
                    </td>
                    <td style={{ textAlign: "right" }}>
                      {line.amount} {line.currency}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
          {Object.entries(o.amounts).map(([currency, m]) => (
            <p key={currency} className="small">
              {t("cabinet.amounts", { charged: m.charged, paid: m.paid, to_pay: m.to_pay, currency })}
              {Number(m.overpaid) > 0 ? ` · ${t("cabinet.overpaid", { amount: m.overpaid, currency })}` : ""}
            </p>
          ))}
        </Card>
      ))}
    </div>
  );
}

function DocumentsList() {
  const { t } = useTranslation();
  const docs = useApi<{ documents: CabinetDocument[] }>(["cabinet-documents"], "/cabinet/documents");
  return (
    <div className="page">
      <h1>{t("cabinet.documents")}</h1>
      <Card>
        {docs.isLoading ? <Loading /> : null}
        {docs.data?.documents.length === 0 ? <p className="muted">{t("cabinet.no_documents")}</p> : null}
        {(docs.data?.documents ?? []).map((d) => (
          <Link key={d.id} className="list-link" to={`/documents/${d.id}`}>
            <span>
              <strong>
                № {d.number} {d.title}
              </strong>{" "}
              <span className="muted small">
                {d.company.display_name} · {d.document_date}
              </span>
              {d.signer ? (
                <>
                  {" "}
                  <span className={`badge ${d.signer.state === "pending" ? "warn" : d.signer.state === "signed" ? "ok" : ""}`}>
                    {t(`cabinet.signer_${d.signer.state}`)}
                  </span>
                </>
              ) : null}
            </span>
          </Link>
        ))}
      </Card>
    </div>
  );
}

function DocumentView() {
  const { t } = useTranslation();
  const { id = "" } = useParams();
  const queryClient = useQueryClient();
  const doc = useApi<{ document: CabinetDocument }>(["cabinet-document", id], `/cabinet/documents/${id}`);
  const askCode = useCommand<Record<string, never>, { sent_to: string }>(`/cabinet/documents/${id}/code`);
  const decide = useCommand<{ code: string; decision: string; comment: string }, unknown>(
    `/cabinet/documents/${id}/decide`,
  );
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [comment, setComment] = useState("");
  if (doc.isLoading) return <div className="page"><Loading /></div>;
  const d = doc.data?.document;
  if (!d) return <div className="page"><ErrorMessage error={doc.error as never} /></div>;

  async function answer(decision: "sign" | "decline") {
    if ((await decide.run({ code, decision, comment })) !== null) {
      setSentTo(null);
      setCode("");
      await queryClient.invalidateQueries({ queryKey: ["cabinet-document", id] });
      await queryClient.invalidateQueries({ queryKey: ["cabinet-documents"] });
    }
  }

  return (
    <div className="page">
      <h1>
        № {d.number} {d.title}
      </h1>
      <p className="muted">
        {d.company.display_name} · {d.kind || t(`documents.kind_${d.kind_code}`)} · {d.document_date}
        {d.version_number ? ` · ${t("cabinet.version", { number: d.version_number })}` : ""}
      </p>
      {d.has_file && d.file_id ? (
        <p>
          <a href={`/api/files/${d.file_id}`} target="_blank" rel="noreferrer">
            {d.file_name || t("cabinet.open_file")}
          </a>
        </p>
      ) : null}
      {d.content ? (
        <Card>
          <div className="document-content" dangerouslySetInnerHTML={{ __html: d.content }} />
        </Card>
      ) : null}
      {d.signer ? (
        <Card title={t("cabinet.signing")}>
          <p>
            <span className={`badge ${d.signer.state === "pending" ? "warn" : d.signer.state === "signed" ? "ok" : ""}`}>
              {t(`cabinet.signer_${d.signer.state}`)}
            </span>
          </p>
          {d.signer.state === "pending" ? (
            sentTo ? (
              <div className="stack">
                <p className="small">{t("cabinet.code_sent", { email: sentTo })}</p>
                <Field label={t("cabinet.code")}>
                  <input value={code} inputMode="numeric" onChange={(e) => setCode(e.target.value)} />
                </Field>
                <Field label={t("cabinet.comment")}>
                  <input value={comment} onChange={(e) => setComment(e.target.value)} />
                </Field>
                <div className="actions">
                  <button type="button" disabled={!code || decide.pending} onClick={() => void answer("sign")}>
                    {t("cabinet.sign")}
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    disabled={!code || decide.pending}
                    onClick={() => void answer("decline")}
                  >
                    {t("cabinet.decline")}
                  </button>
                </div>
              </div>
            ) : (
              <div className="actions">
                <button
                  type="button"
                  disabled={askCode.pending}
                  onClick={async () => {
                    const sent = await askCode.run({});
                    if (sent) setSentTo(sent.sent_to);
                  }}
                >
                  {t("cabinet.get_code")}
                </button>
              </div>
            )
          ) : null}
          <ErrorMessage error={askCode.error ?? decide.error} />
        </Card>
      ) : null}
    </div>
  );
}

function Garage() {
  const { t } = useTranslation();
  const garage = useApi<{
    vehicles: { vehicle_id: string; name: string; make: string; model: string; plate: string }[];
  }>(["cabinet-garage"], "/cabinet/garage");
  return (
    <div className="page">
      <h1>{t("cabinet.garage")}</h1>
      <Card>
        {garage.isLoading ? <Loading /> : null}
        {garage.data?.vehicles.length === 0 ? <p className="muted">{t("cabinet.no_vehicles")}</p> : null}
        {(garage.data?.vehicles ?? []).length ? (
          <table className="table">
            <thead>
              <tr>
                <th>{t("cabinet.vehicle_name")}</th>
                <th>{t("cabinet.make")}</th>
                <th>{t("cabinet.model")}</th>
                <th>{t("cabinet.plate")}</th>
              </tr>
            </thead>
            <tbody>
              {(garage.data?.vehicles ?? []).map((v) => (
                <tr key={v.vehicle_id}>
                  <td>{v.name}</td>
                  <td>{v.make || "—"}</td>
                  <td>{v.model || "—"}</td>
                  <td>{v.plate || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
        <p className="muted small">{t("cabinet.garage_hint")}</p>
      </Card>
    </div>
  );
}

function Requests() {
  const { t } = useTranslation();
  return (
    <div className="page">
      <LinkRequests base="/profile-links" title={t("links.my_title")} />
      <OwnerRequests base="/owner-requests" title={t("owner_requests.my_title")} />
    </div>
  );
}
