/** Documents of a vehicle kept by the company in its card (customer's decision of 12.11.2026):
 * the name, the series and the number, the start and the end of the term, an optional JPEG or PDF
 * file. The main tab shows them; they are added, changed and archived in the settings. */
import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { ApiError, uploadFile } from "../../api/client";
import type { CardDocument, Vehicle } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, IconButton, Modal } from "../../components/ui";
import { useCan, useCompany } from "../../layout/CompanyShell";

export function useDay() {
  const { i18n } = useTranslation();
  return (day: string) => new Date(`${day}T00:00:00`).toLocaleDateString(i18n.language);
}

type Details = { number: string; issued_by?: string; valid_from: string | null; valid_until: string | null };

/** «AB 123456 · from … · until …» — only what is filled. */
export function useDetails() {
  const { t } = useTranslation();
  const day = useDay();
  return (d: Details) =>
    [
      d.number,
      d.issued_by ? t("vehicles.document_issued_by_short", { who: d.issued_by }) : "",
      d.valid_from ? t("documents.from_date", { date: day(d.valid_from) }) : "",
      d.valid_until ? t("documents.until_date", { date: day(d.valid_until) }) : "",
    ]
      .filter(Boolean)
      .join(" · ");
}

function FileLink({ vehicle, document }: { vehicle: Vehicle; document: CardDocument }) {
  const { t } = useTranslation();
  const company = useCompany();
  if (!document.has_file) return null;
  return (
    <a
      href={`/api/c/${company.public_id}/vehicles/${vehicle.id}/card-documents/${document.id}/file`}
      target="_blank"
      rel="noreferrer"
    >
      {t("vehicles.document_file")}
    </a>
  );
}

/** The active documents, read only: the second column of the main tab. */
export function VehicleDocumentsList({ vehicle }: { vehicle: Vehicle }) {
  const { t } = useTranslation();
  const details = useDetails();
  const items = (vehicle.card_documents ?? []).filter((d) => d.state === "active");
  if (items.length === 0) return <p className="muted">{t("vehicles.documents_none")}</p>;
  return (
    <ul className="plain document-summary">
      {items.map((d) => (
        <li key={d.id}>
          <strong>{d.name}</strong>
          <span className="muted small">{details(d) ? ` · ${details(d)}` : ""}</span>{" "}
          <FileLink vehicle={vehicle} document={d} />
        </li>
      ))}
    </ul>
  );
}

export type DocumentDraft = {
  name: string;
  number: string;
  issued_by?: string;
  valid_from: string | null;
  valid_until: string | null;
  has_file: boolean;
  file_name: string;
};

/** The form of a document of a vehicle, the same for a company and for the owner: the name, the
 * series and number in one field, the start and the end of the term, an optional JPEG or PDF.
 * `save` gets the body of the command and answers whether it was accepted. */
export function DocumentForm({
  initial,
  save,
  error,
  onCancel,
}: {
  initial: DocumentDraft | null;
  save: (body: Record<string, unknown>) => Promise<boolean>;
  error: ApiError | null;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState({
    name: initial?.name ?? "",
    number: initial?.number ?? "",
    issued_by: initial?.issued_by ?? "",
    valid_from: initial?.valid_from ?? "",
    valid_until: initial?.valid_until ?? "",
  });
  const [file, setFile] = useState<File | null>(null);
  const [keepFile, setKeepFile] = useState(Boolean(initial?.has_file));
  const [uploadError, setUploadError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (key: keyof typeof draft) => (event: { target: { value: string } }) =>
    setDraft({ ...draft, [key]: event.target.value });

  async function submit(event: FormEvent) {
    event.preventDefault();
    setUploadError(null);
    setBusy(true);
    try {
      const body: Record<string, unknown> = {
        ...draft,
        valid_from: draft.valid_from || null,
        valid_until: draft.valid_until || null,
      };
      if (file) {
        try {
          body.file_id = (await uploadFile("vehicle_document", file)).id;
          body.file_name = file.name;
        } catch (caught) {
          setUploadError(caught instanceof ApiError ? caught : new ApiError(0, null));
          return;
        }
      } else if (initial && keepFile) {
        body.keep_file = true;
      }
      await save(body);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit}>
      <Field label={t("vehicles.document_name")} hint={t("vehicles.document_name_hint")}>
        <input autoFocus required maxLength={200} value={draft.name} onChange={set("name")} />
      </Field>
      <Field label={t("vehicles.document_number")}>
        <input maxLength={150} value={draft.number} onChange={set("number")} />
      </Field>
      <Field label={t("vehicles.document_issued_by")}>
        <input maxLength={200} value={draft.issued_by} onChange={set("issued_by")} />
      </Field>
      <div className="row">
        <Field label={t("vehicles.document_valid_from")}>
          <input type="date" value={draft.valid_from} onChange={set("valid_from")} />
        </Field>
        <Field label={t("vehicles.document_valid_to")}>
          <input type="date" value={draft.valid_until} onChange={set("valid_until")} />
        </Field>
      </div>
      {initial?.has_file && keepFile && !file ? (
        <p>
          {t("vehicles.document_file")}
          {initial.file_name ? `: ${initial.file_name}` : ""}{" "}
          <IconButton icon="trash" label={t("vehicles.document_file_remove")} onClick={() => setKeepFile(false)} />
        </p>
      ) : null}
      <Field label={t("vehicles.document_file_attach")} hint={t("vehicles.document_file_hint")}>
        <input type="file" accept="image/jpeg,application/pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
      </Field>
      <ErrorMessage error={uploadError ?? error} />
      <div className="actions">
        <button type="submit" disabled={busy || !draft.name.trim()}>
          {t("common.save")}
        </button>
        <button type="button" className="secondary" onClick={onCancel}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}

/** Documents to pass as copies: all, some or none are ticked. */
export function DocumentChecklist({
  items,
  chosen,
  onChange,
}: {
  items: (Details & { id: string; name: string })[];
  chosen: string[];
  onChange: (ids: string[]) => void;
}) {
  const { t } = useTranslation();
  const details = useDetails();
  const all = items.length > 0 && chosen.length === items.length;
  return (
    <div className="stack" style={{ gap: 4 }}>
      {items.length > 1 ? (
        <label className="small">
          <input type="checkbox" checked={all} onChange={() => onChange(all ? [] : items.map((d) => d.id))} />{" "}
          {t("vehicles.documents_pass_all")}
        </label>
      ) : null}
      {items.map((d) => (
        <label key={d.id}>
          <input
            type="checkbox"
            checked={chosen.includes(d.id)}
            onChange={() => onChange(chosen.includes(d.id) ? chosen.filter((id) => id !== d.id) : [...chosen, d.id])}
          />{" "}
          {d.name}
          <span className="muted small">{details(d) ? ` · ${details(d)}` : ""}</span>
        </label>
      ))}
    </div>
  );
}

function CardDocumentForm({
  vehicle,
  document,
  onDone,
}: {
  vehicle: Vehicle;
  document: CardDocument | null;
  onDone: () => void;
}) {
  const company = useCompany();
  const client = useQueryClient();
  const base = `/c/${company.public_id}/vehicles/${vehicle.id}/card-documents`;
  const add = useCommand<Record<string, unknown>, unknown>(base);
  const change = useCommand<Record<string, unknown>, unknown>(`${base}/${document?.id ?? ""}`, "PUT");
  return (
    <DocumentForm
      initial={document}
      error={add.error ?? change.error}
      onCancel={onDone}
      save={async (body) => {
        const result = document
          ? await change.run({ ...body, expected_version: document.version })
          : await add.run(body);
        if (result === null) return false;
        await client.invalidateQueries({ queryKey: ["vehicle", company.public_id, vehicle.id] });
        onDone();
        return true;
      }}
    />
  );
}

/** The settings of the card: every document with the buttons to change it and to send it to the
 * archive, and the button that adds a new one. */
export function VehicleDocumentsSettings({ vehicle }: { vehicle: Vehicle }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const details = useDetails();
  const [editing, setEditing] = useState<CardDocument | "new" | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const archive = useCommand<{ id: string; expected_version: number }, unknown>(
    (body) => `/c/${company.public_id}/vehicles/${vehicle.id}/card-documents/${body.id}/archive`,
  );
  const all = vehicle.card_documents ?? [];
  const archived = all.filter((d) => d.state === "archived");
  const shown = showArchived ? all : all.filter((d) => d.state === "active");
  const editable = can("vehicle.edit") && vehicle.state === "active";

  async function toArchive(d: CardDocument) {
    if (!window.confirm(t("vehicles.document_archive_confirm", { name: d.name }))) return;
    if ((await archive.run({ id: d.id, expected_version: d.version })) !== null) {
      await client.invalidateQueries({ queryKey: ["vehicle", company.public_id, vehicle.id] });
    }
  }

  return (
    <Card title={t("vehicles.documents_title")}>
      {shown.length === 0 ? <p className="muted">{t("vehicles.documents_none")}</p> : null}
      {shown.length ? (
        <table>
          <tbody>
            {shown.map((d) => (
              <tr key={d.id}>
                <td>
                  <strong>{d.name}</strong>{" "}
                  {d.state === "archived" ? <span className="badge">{t("vehicles.document_archived")}</span> : null}
                </td>
                <td className="small">{details(d) || "—"}</td>
                <td className="small">
                  <FileLink vehicle={vehicle} document={d} />
                </td>
                <td className="row-actions">
                  {editable && d.state === "active" ? (
                    <>
                      <IconButton icon="edit" label={`${t("common.edit")}: ${d.name}`} onClick={() => setEditing(d)} />
                      <IconButton
                        icon="archive"
                        label={`${t("vehicles.document_to_archive")}: ${d.name}`}
                        disabled={archive.pending}
                        onClick={() => void toArchive(d)}
                      />
                    </>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      <ErrorMessage error={archive.error} />
      <div className="actions">
        {editable ? (
          <button type="button" className="secondary" onClick={() => setEditing("new")}>
            {t("vehicles.document_add")}
          </button>
        ) : null}
        {archived.length ? (
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
          <CardDocumentForm vehicle={vehicle} document={editing === "new" ? null : editing} onDone={() => setEditing(null)} />
        </Modal>
      ) : null}
    </Card>
  );
}
