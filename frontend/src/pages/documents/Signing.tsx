import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useParams } from "react-router";

import { ApiError, uploadFile } from "../../api/client";
import { useApi } from "../../api/queries";
import type { CompanyDocument, DocumentSigner, DocumentSigning, SigningLink } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { useCan, useCompany } from "../../layout/CompanyShell";
import { deviceTimeZone, formatMoment } from "../../time";
import { useDocumentKinds, useKindName } from "./DocumentsPage";
import { OnScreenSigning } from "./OnScreen";

type SigningResult = { signing: DocumentSigning };

export function SigningBadge({ state }: { state: CompanyDocument["signing"]["state"] }) {
  const { t } = useTranslation();
  if (state === "none") return null;
  const tone = state === "signed" ? "ok" : state === "declined" ? "danger" : "warn";
  return <span className={`badge ${tone}`}>{t(`documents.signing_${state}`)}</span>;
}

/** Signers of the versions sent for signing and their answers (§ 18.3.3–18.3.7). */
export function SigningCard({ d }: { d: CompanyDocument }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const queryClient = useQueryClient();
  const base = `/c/${company.public_id}/documents/${d.id}`;
  const query = useApi<SigningResult>(["document", company.public_id, d.id, "signing"], `${base}/signing`);
  const send = useCommand<Record<string, unknown>, SigningResult>(`${base}/signing`);
  const [chosen, setChosen] = useState<Record<string, boolean> | null>(null);
  const signing = query.data?.signing;
  if (query.isLoading) return <Loading />;
  if (!signing) return <ErrorMessage error={query.error instanceof ApiError ? query.error : null} />;
  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["document"] });
    await queryClient.invalidateQueries({ queryKey: ["documents"] });
  };
  const active = d.state === "active";
  const canSend = active && can("documents.upload") && signing.current_version !== null && signing.current_version !== signing.sent_version;
  const keyOf = (c: { role: string; id: string }) => `${c.role}:${c.id}`;
  const picked =
    chosen ??
    Object.fromEntries(
      signing.candidates.map((c) => [keyOf(c), c.role !== "company" && signing.template_signers.includes(c.role)]),
    );
  const people = signing.candidates.filter((c) => picked[keyOf(c)]);
  const sent = signing.signers.filter((x) => x.version_number === signing.sent_version);
  const former = signing.signers.filter((x) => x.version_number !== signing.sent_version);

  async function sendNow() {
    const result = await send.run({ expected_version: d.version, signers: people.map((c) => ({ role: c.role, id: c.id })) });
    if (result) {
      setChosen(null);
      await refresh();
    }
  }

  return (
    <Card title={t("documents.signing")}>
      <p className="small">
        {signing.sent_version ? t("documents.signing_of_version", { number: signing.sent_version }) : t("documents.signing_not_sent")}{" "}
        <SigningBadge state={signing.status.state} />
        {" · "}
        {t(`documents.methods_${signing.methods}`)}
      </p>
      {sent.map((x) => (
        <SignerRow key={x.id} d={d} signer={x} methods={signing.methods} onScreen={signing.on_screen} open={active} onDone={refresh} />
      ))}
      {canSend ? (
        <div style={{ marginTop: 12 }}>
          <strong>{t("documents.send_version", { number: signing.current_version })}</strong>
          {signing.sent_version ? <p className="small muted">{t("documents.send_replaces", { number: signing.sent_version })}</p> : null}
          {signing.template_signers.length ? (
            <p className="small muted">
              {t("documents.template_signers")}: {signing.template_signers.map((r) => t(`documents.signer_${r}`)).join(", ")}
            </p>
          ) : null}
          {signing.candidates.length === 0 ? <p className="small muted">{t("documents.no_candidates")}</p> : null}
          {signing.candidates.map((c) => (
            <label key={keyOf(c)} className="small" style={{ display: "block" }}>
              <input
                type="checkbox"
                checked={Boolean(picked[keyOf(c)])}
                onChange={(e) => setChosen({ ...picked, [keyOf(c)]: e.target.checked })}
              />{" "}
              {t(`documents.signer_${c.role}`)}: {c.name}
            </label>
          ))}
          <div className="actions">
            <button type="button" disabled={send.pending || people.length === 0} onClick={() => void sendNow()}>
              {t("documents.send_button")}
            </button>
          </div>
          <ErrorMessage error={send.error} />
        </div>
      ) : null}
      {former.length ? (
        <details style={{ marginTop: 12 }}>
          <summary className="small">{t("documents.former_signers")}</summary>
          {former.map((x) => (
            <SignerRow key={x.id} d={d} signer={x} methods={signing.methods} onScreen={false} open={false} onDone={refresh} />
          ))}
        </details>
      ) : null}
    </Card>
  );
}

function SignerRow({
  d,
  signer,
  methods,
  onScreen,
  open,
  onDone,
}: {
  d: CompanyDocument;
  signer: DocumentSigner;
  methods: DocumentSigning["methods"];
  onScreen: boolean;
  open: boolean;
  onDone: () => Promise<void>;
}) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const zone = company.timezone ?? deviceTimeZone();
  const base = `/c/${company.public_id}/documents/${d.id}/signers/${signer.id}`;
  const code = useCommand<Record<string, unknown>, SigningResult>(`${base}/code`);
  const decide = useCommand<Record<string, unknown>, SigningResult>(`${base}/decide`);
  const invite = useCommand<Record<string, unknown>, SigningResult>(`${base}/invite`);
  const revoke = useCommand<Record<string, unknown>, SigningResult>(`${base}/revoke`);
  const paper = useCommand<Record<string, unknown>, SigningResult>(`${base}/paper`);
  const [codeSent, setCodeSent] = useState(false);
  const [value, setValue] = useState("");
  const [comment, setComment] = useState("");
  const [email, setEmail] = useState(signer.emails[0] ?? "");
  const [days, setDays] = useState("7");
  const [scan, setScan] = useState<File | null>(null);
  const [signedOn, setSignedOn] = useState("");
  const [uploadError, setUploadError] = useState<ApiError | null>(null);
  const [presenting, setPresenting] = useState(false);
  const pending = open && signer.state === "pending";
  const content = (d.versions ?? []).find((v) => v.number === signer.version_number)?.content ?? "";
  const inApp = methods !== "paper";
  const onPaper = methods !== "in_app";
  const done = (result: unknown) => (result ? onDone() : undefined);

  async function enterPaper() {
    if (!scan) return;
    setUploadError(null);
    let fileId: string;
    try {
      fileId = (await uploadFile("company_document", scan)).id;
    } catch (caught) {
      setUploadError(caught instanceof ApiError ? caught : new ApiError(0, null));
      return;
    }
    await done(await paper.run({ file_id: fileId, signed_on: signedOn }));
  }

  return (
    <div className="small" style={{ borderTop: "1px solid var(--border, #ddd)", padding: "8px 0" }}>
      <div>
        <strong>{t(`documents.signer_${signer.role}`)}</strong>: {signer.name} ·{" "}
        <span className={`badge ${signer.state === "signed" ? "ok" : signer.state === "declined" ? "danger" : "warn"}`}>
          {t(`documents.signer_state_${signer.state}`)}
        </span>
        {signer.method ? ` · ${t(`documents.method_${signer.method}`)}` : ""}
        {signer.decided_at ? ` · ${formatMoment(signer.decided_at, zone, i18n.language)}` : ""}
        {signer.confirmed_contact ? ` · ${signer.confirmed_contact}` : ""}
        {signer.paper_signed_on ? ` · ${t("documents.paper_signed_on")}: ${signer.paper_signed_on}` : ""}
        {signer.paper_file_id ? (
          <>
            {" · "}
            <a href={`/api/files/${signer.paper_file_id}`} target="_blank" rel="noreferrer">
              {t("documents.paper_scan")}
            </a>
          </>
        ) : null}
        {signer.entered_at && signer.method !== "on_screen"
          ? ` · ${t("documents.entered_at", { at: formatMoment(signer.entered_at, zone, i18n.language) })}`
          : ""}
        {signer.method === "on_screen" ? ` · ${t(signer.presented_by_me ? "documents.presented_by_me" : "documents.presented_by_employee")}` : ""}
      </div>
      {signer.signature_file_id ? <SignatureImage fileId={signer.signature_file_id} /> : null}
      {signer.comment ? <div className="muted">{t("documents.signer_comment")}: {signer.comment}</div> : null}
      {pending && signer.role !== "company" && onScreen && content && can("documents.upload") ? (
        <div className="actions">
          <button type="button" onClick={() => setPresenting(true)}>
            {t("documents.on_screen_start")}
          </button>
        </div>
      ) : null}
      {presenting ? (
        <OnScreenSigning
          documentId={d.id}
          title={d.title}
          content={content}
          signer={signer}
          onClose={() => setPresenting(false)}
          onDone={async () => {
            setPresenting(false);
            await onDone();
          }}
        />
      ) : null}
      {pending && signer.is_me && inApp && can("documents.sign") ? (
        <div className="row">
          {!codeSent ? (
            <div className="actions">
              <button type="button" disabled={code.pending} onClick={() => void code.run({}).then((r) => setCodeSent(Boolean(r)))}>
                {t("documents.get_code")}
              </button>
            </div>
          ) : (
            <>
              <Field label={t("documents.code")} hint={t("documents.code_hint")}>
                <input inputMode="numeric" value={value} onChange={(e) => setValue(e.target.value)} />
              </Field>
              <Field label={t("documents.signer_comment")}>
                <input value={comment} onChange={(e) => setComment(e.target.value)} />
              </Field>
              <div className="actions">
                <button type="button" disabled={decide.pending || !value} onClick={() => void decide.run({ code: value, decision: "sign", comment }).then(done)}>
                  {t("documents.sign")}
                </button>
                <button type="button" className="danger" disabled={decide.pending || !value} onClick={() => void decide.run({ code: value, decision: "decline", comment }).then(done)}>
                  {t("documents.decline")}
                </button>
              </div>
            </>
          )}
          <ErrorMessage error={code.error ?? decide.error} />
        </div>
      ) : null}
      {pending && signer.role !== "company" && inApp && can("documents.upload") ? (
        <div className="row">
          {signer.link ? (
            <span>
              {t("documents.link_active", { email: signer.link.email, until: formatMoment(signer.link.expires_at, zone, i18n.language) })}{" "}
              <button type="button" className="link" disabled={revoke.pending} onClick={() => void revoke.run({}).then(done)}>
                {t("documents.revoke_link")}
              </button>
            </span>
          ) : null}
          {signer.emails.length === 0 ? (
            <span className="muted">{t("documents.no_email_in_card")}</span>
          ) : (
            <>
              <Field label={t("documents.link_email")}>
                <select value={email} onChange={(e) => setEmail(e.target.value)}>
                  {signer.emails.map((x) => (
                    <option key={x} value={x}>
                      {x}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label={t("documents.link_days")}>
                <input type="number" min={1} max={90} value={days} onChange={(e) => setDays(e.target.value)} />
              </Field>
              <div className="actions">
                <button type="button" className="secondary" disabled={invite.pending || !email} onClick={() => void invite.run({ email, days: Number(days) }).then(done)}>
                  {signer.link ? t("documents.send_link_again") : t("documents.send_link")}
                </button>
              </div>
            </>
          )}
          <ErrorMessage error={invite.error ?? revoke.error} />
        </div>
      ) : null}
      {pending && onPaper && can("documents.upload") ? (
        <div className="row">
          <Field label={t("documents.paper_scan")} hint={t("documents.file_hint")}>
            <input type="file" accept=".pdf,image/jpeg,image/png,image/webp" onChange={(e) => setScan(e.target.files?.[0] ?? null)} />
          </Field>
          <Field label={t("documents.paper_signed_on")}>
            <input type="date" value={signedOn} onChange={(e) => setSignedOn(e.target.value)} />
          </Field>
          <div className="actions">
            <button type="button" className="secondary" disabled={paper.pending || !scan || !signedOn} onClick={() => void enterPaper()}>
              {t("documents.enter_paper")}
            </button>
          </div>
          <ErrorMessage error={uploadError ?? paper.error} />
        </div>
      ) : null}
    </div>
  );
}

/** A signature drawn on the screen (§ 10.2.2). */
export function SignatureImage({ fileId }: { fileId: string }) {
  const { t } = useTranslation();
  return <img className="signature-image" src={`/api/files/${fileId}`} alt={t("documents.signature_image")} />;
}

/** The signers of one version with their answers and drawn signatures, for the print view. */
export function VersionSignatures({ documentId, version }: { documentId: string; version: number }) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const zone = company.timezone ?? deviceTimeZone();
  const query = useApi<SigningResult>(["document", company.public_id, documentId, "signing"], `/c/${company.public_id}/documents/${documentId}/signing`);
  const signers = (query.data?.signing.signers ?? []).filter((x) => x.version_number === version);
  if (signers.length === 0) return null;
  return (
    <div className="document-content print-signatures">
      <h3>{t("documents.signatures")}</h3>
      {signers.map((x) => (
        <div key={x.id} className="print-signature">
          <div>
            <strong>{t(`documents.signer_${x.role}`)}</strong>: {x.name} — {t(`documents.signer_state_${x.state}`)}
            {x.method ? `, ${t(`documents.method_${x.method}`)}` : ""}
            {x.decided_at ? `, ${formatMoment(x.decided_at, zone, i18n.language)}` : ""}
            {x.paper_signed_on ? `, ${t("documents.paper_signed_on")}: ${x.paper_signed_on}` : ""}
          </div>
          {x.comment ? <div>{t("documents.signer_comment")}: {x.comment}</div> : null}
          {x.signature_file_id ? <SignatureImage fileId={x.signature_file_id} /> : null}
        </div>
      ))}
    </div>
  );
}

/** Allowed ways of signing of each kind (§ 18.4.2). */
export function KindSigningCard() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const queryClient = useQueryClient();
  const kinds = useDocumentKinds();
  const kindName = useKindName();
  const query = useApi<{ kinds: { kind_id: string; methods: DocumentSigning["methods"]; on_screen: boolean }[] }>(
    ["document-kind-signing", company.public_id],
    `/c/${company.public_id}/document-kinds/signing`,
  );
  const change = useCommand<Record<string, unknown>, unknown>(`/c/${company.public_id}/document-kinds/signing`);
  const editable = can("documents.templates");
  return (
    <Card title={t("documents.kind_methods")}>
      <table>
        <tbody>
          {(query.data?.kinds ?? []).map((x) => (
            <tr key={x.kind_id}>
              <td>{kindName(kinds.data?.kinds.find((k) => k.id === x.kind_id))}</td>
              <td>
                <select
                  value={x.methods}
                  disabled={!editable || change.pending}
                  onChange={(e) =>
                    void change
                      .run({ kind_id: x.kind_id, methods: e.target.value })
                      .then((r) => (r ? queryClient.invalidateQueries({ queryKey: ["document-kind-signing"] }) : undefined))
                  }
                >
                  {(["both", "in_app", "paper"] as const).map((m) => (
                    <option key={m} value={m}>
                      {t(`documents.methods_${m}`)}
                    </option>
                  ))}
                </select>
              </td>
              <td className="small">
                {/* § 18.4.2: the signing on the screen applies to an act of inspection only. */}
                {kinds.data?.kinds.find((k) => k.id === x.kind_id)?.code === "inspection_act" ? (
                  <label>
                    <input
                      type="checkbox"
                      checked={x.on_screen}
                      disabled={!editable || change.pending}
                      onChange={(e) =>
                        void change
                          .run({ kind_id: x.kind_id, methods: x.methods, on_screen: e.target.checked })
                          .then((r) => (r ? queryClient.invalidateQueries({ queryKey: ["document-kind-signing"] }) : undefined))
                      }
                    />{" "}
                    {t("documents.methods_on_screen")}
                  </label>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <ErrorMessage error={change.error} />
    </Card>
  );
}

/** The page of a personal link: only this version and the answer (§ 18.3.9–18.3.10). */
export function SignByLinkPage() {
  const { t } = useTranslation();
  const { token = "" } = useParams();
  const query = useApi<{ link: SigningLink }>(["signing-link", token], `/sign/${token}`);
  const code = useCommand<Record<string, unknown>, { sent_to: string }>(`/sign/${token}/code`);
  const decide = useCommand<Record<string, unknown>, { state: string }>(`/sign/${token}/decide`);
  const [sentTo, setSentTo] = useState("");
  const [value, setValue] = useState("");
  const [comment, setComment] = useState("");
  const [answer, setAnswer] = useState("");
  const link = query.data?.link;

  async function submit(decision: "sign" | "decline") {
    const result = await decide.run({ code: value, decision, comment });
    if (result) setAnswer(result.state);
  }

  return (
    <div className="narrow" style={{ maxWidth: 820 }}>
      {query.isLoading ? <Loading /> : null}
      {!link && !query.isLoading ? <ErrorMessage error={query.error instanceof ApiError ? query.error : null} /> : null}
      {link ? (
        <>
          <h1>{link.title}</h1>
          <p className="small muted">
            {link.company} · {t("documents.number", { number: link.number })} · {t("documents.version", { number: link.version_number })}
          </p>
          <Card>
            {link.content ? <div className="document-content" dangerouslySetInnerHTML={{ __html: link.content }} /> : null}
            {link.has_file ? (
              <a href={`/api/sign/${token}/file`} target="_blank" rel="noreferrer">
                {link.file_name || t("documents.open_file")}
              </a>
            ) : null}
          </Card>
          <Card title={t("documents.signer_answer", { name: link.signer_name })}>
            {answer ? (
              <p>{t(`documents.link_answer_${answer}`)}</p>
            ) : !sentTo ? (
              <>
                <p className="small">{t("documents.link_code_hint", { email: link.email })}</p>
                <div className="actions">
                  <button type="button" disabled={code.pending} onClick={() => void code.run({}).then((r) => (r ? setSentTo(r.sent_to) : undefined))}>
                    {t("documents.get_code")}
                  </button>
                </div>
              </>
            ) : (
              <div className="row">
                <Field label={t("documents.code")} hint={t("documents.code_sent_to", { email: sentTo })}>
                  <input inputMode="numeric" value={value} onChange={(e) => setValue(e.target.value)} />
                </Field>
                <Field label={t("documents.signer_comment")}>
                  <input value={comment} onChange={(e) => setComment(e.target.value)} />
                </Field>
                <div className="actions">
                  <button type="button" disabled={decide.pending || !value} onClick={() => void submit("sign")}>
                    {t("documents.sign")}
                  </button>
                  <button type="button" className="danger" disabled={decide.pending || !value} onClick={() => void submit("decline")}>
                    {t("documents.decline")}
                  </button>
                </div>
              </div>
            )}
            <ErrorMessage error={code.error ?? decide.error} />
          </Card>
        </>
      ) : null}
    </div>
  );
}
