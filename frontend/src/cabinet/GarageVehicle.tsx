import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router";

import { useApi } from "../api/queries";
import type { GarageVehicleDetail, GlobalDocument, TransferReversal } from "../api/types";
import { useCommand } from "../api/useCommand";
import { Card, ErrorMessage, Field, IconButton, Loading, Modal } from "../components/ui";
import { DocumentChecklist, DocumentForm, useDetails } from "../pages/vehicles/VehicleDocuments";
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

/** The owner's documents of the vehicle in the same form as a company keeps them (decision of
 * 12.11.2026): the name, the series and number, the dates, an optional file; a change is kept as a
 * new version (§ 8.2.3). Copies are passed to a company whose management is confirmed. */
function Documents({ detail }: { detail: GarageVehicleDetail }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const describe = useDetails();
  const base = `/cabinet/garage/${detail.vehicle_id}`;
  const [editing, setEditing] = useState<GlobalDocument | "new" | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [passed, setPassed] = useState<string[]>([]);
  const managers = detail.managers.filter((m) => m.confirmed);
  const [target, setTarget] = useState("");
  const add = useCommand<Record<string, unknown>, unknown>(`${base}/documents`);
  const archive = useCommand<{ id: string }, unknown>((body) => `${base}/documents/${body.id}/archive`);
  const pass = useCommand<{ card_id: string; document_ids: string[] }, unknown>(`${base}/documents/pass`);
  const [passedTo, setPassedTo] = useState("");
  // The body carries the address of the copy; the server reads only the decision fields.
  const decide = useCommand<{ path: string; decision: string; document_id?: string; doc_type?: string }, unknown>(
    (body) => body.path,
  );
  const refresh = () => client.invalidateQueries({ queryKey: ["cabinet-garage", detail.vehicle_id] });
  const row = (d: GlobalDocument) => ({
    number: d.current?.details ?? "",
    valid_from: d.current?.issued_on ?? null,
    valid_until: d.current?.valid_until ?? null,
  });
  const active = detail.documents.filter((d) => !d.archived);
  const shown = showArchived ? detail.documents : active;
  const cardId = target || managers[0]?.card_id || "";

  return (
    <Card title={t("cabinet.documents_title")}>
      <p className="muted small">{t("cabinet.documents_hint")}</p>
      {shown.length === 0 ? <p className="muted">{t("vehicles.documents_none")}</p> : null}
      {shown.length ? (
        <table>
          <tbody>
            {shown.map((d) => (
              <tr key={d.id}>
                <td>
                  <strong>{d.name}</strong>{" "}
                  {d.archived ? <span className="badge">{t("vehicles.document_archived")}</span> : null}
                </td>
                <td className="small">{describe(row(d)) || "—"}</td>
                <td className="small">
                  {d.current?.has_file ? (
                    <a href={`/api${base}/documents/versions/${d.current.id}/file`} target="_blank" rel="noreferrer">
                      {t("vehicles.document_file")}
                    </a>
                  ) : null}
                </td>
                <td className="row-actions">
                  {d.archived ? null : (
                    <>
                      <IconButton icon="edit" label={`${t("common.edit")}: ${d.name}`} onClick={() => setEditing(d)} />
                      <IconButton
                        icon="archive"
                        label={`${t("vehicles.document_to_archive")}: ${d.name}`}
                        disabled={archive.pending}
                        onClick={async () => {
                          if (!window.confirm(t("vehicles.document_archive_confirm", { name: d.name }))) return;
                          if ((await archive.run({ id: d.id })) !== null) await refresh();
                        }}
                      />
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      <ErrorMessage error={archive.error} />
      <div className="actions">
        <button type="button" className="secondary" onClick={() => setEditing("new")}>
          {t("vehicles.document_add")}
        </button>
        {detail.documents.some((d) => d.archived) ? (
          <button type="button" className="toggle-chip" aria-pressed={showArchived} onClick={() => setShowArchived(!showArchived)}>
            {t("vehicles.documents_archive")}
          </button>
        ) : null}
      </div>
      {editing ? (
        <Modal
          title={editing === "new" ? t("vehicles.document_add") : t("vehicles.document_edit")}
          onClose={() => setEditing(null)}
        >
          <DocumentForm
            initial={
              editing === "new"
                ? null
                : { name: editing.name, ...row(editing), has_file: Boolean(editing.current?.has_file), file_name: "" }
            }
            error={add.error}
            onCancel={() => setEditing(null)}
            save={async (body) => {
              const result = await add.run(editing === "new" ? body : { ...body, document_id: editing.id });
              if (result === null) return false;
              setEditing(null);
              await refresh();
              return true;
            }}
          />
        </Modal>
      ) : null}
      {managers.length && active.length ? (
        <>
          <h3>{t("cabinet.documents_pass_title")}</h3>
          <p className="muted small">{t("cabinet.documents_pass_hint")}</p>
          {managers.length > 1 ? (
            <Field label={t("cabinet.documents_pass_company")}>
              <select value={cardId} onChange={(e) => setTarget(e.target.value)}>
                {managers.map((m) => (
                  <option key={m.card_id} value={m.card_id}>
                    {m.company.display_name}
                  </option>
                ))}
              </select>
            </Field>
          ) : (
            <p>{managers[0]?.company.display_name}</p>
          )}
          <DocumentChecklist
            items={active.map((d) => ({ id: d.id, name: d.name, ...row(d) }))}
            chosen={passed}
            onChange={(ids) => {
              setPassed(ids);
              setPassedTo("");
            }}
          />
          <ErrorMessage error={pass.error} />
          {passedTo ? <p className="badge ok">{t("cabinet.documents_passed", { company: passedTo })}</p> : null}
          <div className="actions">
            <button
              type="button"
              disabled={!passed.length || !cardId || pass.pending}
              onClick={async () => {
                if ((await pass.run({ card_id: cardId, document_ids: passed })) !== null) {
                  setPassedTo(managers.find((m) => m.card_id === cardId)?.company.display_name ?? "");
                  setPassed([]);
                }
              }}
            >
              {t("cabinet.documents_pass_button")}
            </button>
          </div>
        </>
      ) : null}
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
                          const body = { path: `${path}/decide`, decision: "accepted", doc_type: "other" };
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
          <ErrorMessage error={decide.error} />
        </>
      ) : null}
    </Card>
  );
}
