import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router";

import { uploadFile } from "../api/client";
import { useApi } from "../api/queries";
import type { GarageVehicleDetail, TransferReversal } from "../api/types";
import { useCommand } from "../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../components/ui";
import { deviceTimeZone, formatMoment } from "../time";

/** A vehicle of the personal garage (ТЗ § 7.1.2): what the managing companies pass by category
 * (§ 8.3.6, § 8.3.15), the global documents with versions and the companies' copies (§ 8.2.3,
 * § 8.2.7), the works before a sale (§ 9.1.4, § 9.1.16), the seller's requests (§ 8.3.9). */
export function GarageVehicle() {
  const { t, i18n } = useTranslation();
  const { vehicleId = "" } = useParams();
  const key = ["cabinet-garage", vehicleId];
  const detail = useApi<{ vehicle: GarageVehicleDetail }>(key, `/cabinet/garage/${vehicleId}`);
  const when = (value: string | null | undefined) => formatMoment(value ?? null, deviceTimeZone(), i18n.language);
  if (detail.isLoading) return <div className="page"><Loading /></div>;
  const v = detail.data?.vehicle;
  if (!v) return <div className="page"><ErrorMessage error={null} /></div>;
  return (
    <div className="page">
      <p>
        <Link to="/garage">← {t("cabinet.garage")}</Link>
      </p>
      <h1>{v.name}</h1>
      <Card>
        <p>
          {v.vehicle_id} · {[v.make, v.model, v.plate].filter(Boolean).join(" · ") || "—"}
        </p>
        <p className="muted small">{t("cabinet.owner_since", { at: when(v.since) })}</p>
      </Card>
      <Reversals items={v.reversals} />
      {v.managers.map((m) => (
        <Card key={m.company.public_id} title={t("cabinet.manager_title", { company: m.company.display_name })}>
          <p className="small">{m.confirmed ? t("cabinet.manager_confirmed") : t("cabinet.manager_not_confirmed")}</p>
          {m.categories.length === 0 ? <p className="muted small">{t("cabinet.manager_nothing")}</p> : null}
          {m.categories.map((c) => (
            <div key={c.category} style={{ marginBottom: 8 }}>
              <strong>{t(`vehicles.disclosure_${c.category}`)}</strong>{" "}
              {!c.passed ? <span className="badge">{t("cabinet.category_offered")}</span> : null}
              {c.passed && c.until ? <span className="muted small"> · {t("cabinet.category_until", { at: when(c.until) })}</span> : null}
              {c.totals?.length ? (
                <ul className="plain small">
                  {c.totals.map((x, i) => (
                    <li key={i}>
                      {Object.entries(x)
                        .map(([k, val]) => (k === "currency" ? val : `${t(`cabinet.total_${k}`)}: ${val}`))
                        .join(" · ")}
                    </li>
                  ))}
                </ul>
              ) : null}
              {c.records?.length ? (
                <ul className="plain small">
                  {c.records.slice(0, 50).map((r, i) => (
                    <li key={i}>
                      {when(String(r.at ?? ""))} ·{" "}
                      {Object.entries(r)
                        .filter(([k, val]) => k !== "at" && val !== null && val !== "" && typeof val !== "object")
                        .map(([, val]) => String(val))
                        .join(" · ")}
                    </li>
                  ))}
                </ul>
              ) : c.passed && !c.totals ? (
                <p className="muted small">{t("cabinet.category_empty")}</p>
              ) : null}
            </div>
          ))}
        </Card>
      ))}
      <Documents detail={v} />
      {v.seller_history ? (
        <Card title={t("cabinet.seller_history_title", { company: v.seller_history.company })}>
          <p className="muted small">{t("cabinet.seller_history_hint")}</p>
          {v.seller_history.works.length === 0 ? <p className="muted small">{t("cabinet.category_empty")}</p> : null}
          <ul className="plain small">
            {v.seller_history.works.map((w, i) => (
              <li key={i}>
                {when(w.at)} · {w.work}
                {w.odometer ? ` · ${Number(w.odometer)}` : ""}
                {w.materials.length ? ` · ${w.materials.map((m) => `${m.item} ${Number(m.quantity)} ${m.unit}`).join(", ")}` : ""}
              </li>
            ))}
          </ul>
          {v.seller_history.rules.length ? (
            <>
              <h3>{t("cabinet.rules_title")}</h3>
              <ul className="plain small">
                {v.seller_history.rules.map((r, i) => (
                  <li key={i}>
                    {r.name}
                    {r.interval_distance ? ` · ${r.interval_distance}` : ""}
                    {r.interval_days ? ` · ${t("cabinet.rule_days", { days: r.interval_days })}` : ""}
                  </li>
                ))}
              </ul>
            </>
          ) : null}
        </Card>
      ) : null}
    </div>
  );
}

function Reversals({ items }: { items: TransferReversal[] }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [comment, setComment] = useState("");
  const open = items.find((r) => r.state === "open");
  const answer = useCommand<{ decision: string; comment: string }, unknown>(
    `/cabinet/reversals/${open?.id ?? ""}/answer`,
  );
  if (!open) return null;
  async function decide(decision: string) {
    if ((await answer.run({ decision, comment })) !== null) {
      await client.invalidateQueries({ queryKey: ["cabinet-garage"] });
    }
  }
  return (
    <Card title={t("cabinet.reversal_title", { company: open.company.display_name })}>
      <div className="alert warn">{open.reason}</div>
      <p className="muted small">{t("cabinet.reversal_hint")}</p>
      <Field label={t("cabinet.comment")}>
        <input value={comment} onChange={(e) => setComment(e.target.value)} />
      </Field>
      <ErrorMessage error={answer.error} />
      <div className="actions">
        <button type="button" className="secondary" disabled={answer.pending} onClick={() => void decide("accept")}>
          {t("cabinet.reversal_accept")}
        </button>
        <button type="button" disabled={answer.pending} onClick={() => void decide("reject")}>
          {t("cabinet.reversal_reject")}
        </button>
      </div>
    </Card>
  );
}

const DOC_TYPES = ["passport", "registration", "insurance", "other"] as const;

function Documents({ detail }: { detail: GarageVehicleDetail }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const base = `/cabinet/garage/${detail.vehicle_id}`;
  const [docType, setDocType] = useState<string>("insurance");
  const [documentId, setDocumentId] = useState("");
  const [details, setDetails] = useState("");
  const [validUntil, setValidUntil] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const add = useCommand<Record<string, unknown>, unknown>(`${base}/documents`);
  // The body carries the address of the copy; the server reads only the decision fields.
  const decide = useCommand<{ path: string; decision: string; document_id?: string; doc_type?: string }, unknown>(
    (body) => body.path,
  );
  const refresh = () => client.invalidateQueries({ queryKey: ["cabinet-garage", detail.vehicle_id] });

  async function save() {
    setUploading(true);
    try {
      const fileId = file ? (await uploadFile("vehicle_document", file)).id : null;
      const result = await add.run({
        document_id: documentId || null,
        doc_type: documentId ? null : docType,
        details,
        valid_until: validUntil || null,
        file_id: fileId,
      });
      if (result !== null) {
        setDetails("");
        setValidUntil("");
        setFile(null);
        await refresh();
      }
    } finally {
      setUploading(false);
    }
  }

  return (
    <Card title={t("cabinet.documents_title")}>
      <p className="muted small">{t("cabinet.documents_hint")}</p>
      {detail.documents.map((d) => (
        <div key={d.id} style={{ marginBottom: 8 }}>
          <strong>{t(`vehicles.document_type_${d.doc_type}`)}</strong>
          {d.title ? ` · ${d.title}` : ""}
          <ul className="plain small">
            {d.versions.map((v) => (
              <li key={v.id}>
                v{v.number} · {v.details || "—"}
                {v.valid_until ? ` · ${t("vehicles.document_valid_until", { date: v.valid_until })}` : ""}
                {v.source === "company_copy" ? ` · ${t("vehicles.document_from_copy")}` : ""}
                {v.has_file ? (
                  <>
                    {" · "}
                    <a href={`/api${base}/documents/versions/${v.id}/file`} target="_blank" rel="noreferrer">
                      {t("vehicles.document_file")}
                    </a>
                  </>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ))}
      <div className="row">
        <Field label={t("cabinet.document_target")}>
          <select value={documentId} onChange={(e) => setDocumentId(e.target.value)}>
            <option value="">{t("cabinet.document_new")}</option>
            {detail.documents.map((d) => (
              <option key={d.id} value={d.id}>
                {t(`vehicles.document_type_${d.doc_type}`)} {d.title}
              </option>
            ))}
          </select>
        </Field>
        {documentId ? null : (
          <Field label={t("cabinet.document_type")}>
            <select value={docType} onChange={(e) => setDocType(e.target.value)}>
              {DOC_TYPES.map((d) => (
                <option key={d} value={d}>
                  {t(`vehicles.document_type_${d}`)}
                </option>
              ))}
            </select>
          </Field>
        )}
        <Field label={t("cabinet.document_details")}>
          <input value={details} onChange={(e) => setDetails(e.target.value)} />
        </Field>
        <Field label={t("cabinet.document_valid_until")}>
          <input type="date" value={validUntil} onChange={(e) => setValidUntil(e.target.value)} />
        </Field>
        <Field label={t("cabinet.document_file")}>
          <input type="file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </Field>
      </div>
      <ErrorMessage error={add.error} />
      <div className="actions">
        <button type="button" disabled={uploading || add.pending || (!file && !details.trim())} onClick={() => void save()}>
          {t("cabinet.document_add")}
        </button>
      </div>
      {detail.copies.length ? (
        <>
          <h3>{t("cabinet.copies_title")}</h3>
          <p className="muted small">{t("cabinet.copies_hint")}</p>
          <ul className="plain small">
            {detail.copies.map((c) => {
              const path = `${base}/copies/${c.card_id}/${c.version_id}`;
              return (
                <li key={c.version_id} style={{ marginBottom: 6 }}>
                  {c.company} · {c.title} v{c.number} ·{" "}
                  <a href={`/api${path}/file`} target="_blank" rel="noreferrer">
                    {t("vehicles.document_file")}
                  </a>{" "}
                  {c.decision ? (
                    <span className="badge">{t(`vehicles.copy_decision_${c.decision}`)}</span>
                  ) : (
                    <>
                      <button
                        type="button"
                        className="secondary"
                        disabled={decide.pending}
                        onClick={async () => {
                          const body = documentId
                            ? { path: `${path}/decide`, decision: "accepted", document_id: documentId }
                            : { path: `${path}/decide`, decision: "accepted", doc_type: docType };
                          if ((await decide.run(body)) !== null) await refresh();
                        }}
                      >
                        {t("cabinet.copy_accept")}
                      </button>{" "}
                      <button
                        type="button"
                        className="secondary"
                        disabled={decide.pending}
                        onClick={async () => {
                          if ((await decide.run({ path: `${path}/decide`, decision: "kept_private" })) !== null) await refresh();
                        }}
                      >
                        {t("cabinet.copy_keep")}
                      </button>
                    </>
                  )}
                </li>
              );
            })}
          </ul>
          <p className="muted small">{t("cabinet.copy_target_hint")}</p>
          <ErrorMessage error={decide.error} />
        </>
      ) : null}
    </Card>
  );
}
