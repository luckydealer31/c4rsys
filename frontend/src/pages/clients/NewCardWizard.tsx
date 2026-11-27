import { apiList, cardPath as pathOfCard, listPath, type CardType } from "./cardLists";
import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router";

import { ApiError, uploadPages } from "../../api/client";
import { useApi } from "../../api/queries";
import type { Client, Counterparty } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Info } from "../../components/ui";
import { companyPath, useCompany } from "../../layout/CompanyShell";
import { clientName, useMatches } from "./ClientsPage";
import { ContactsEditor, contactValid, contactsOf, phoneAndEmail, type ContactRow } from "./ContactsEditor";

type Step = "kind" | "name" | "birth" | "contacts";

type Form = {
  kind: "person" | "company";
  first_name: string;
  last_name: string;
  middle_name: string;
  birth_date: string;
  company_name: string;
  contacts: ContactRow[];
  comment: string;
};

/** What was read from a photo of a document: the kind of the card, which document it is and the
 * fields of the card the creation takes (customer's decision of 28.11.2026). */
type Reading = { kind: "person" | "company"; document_kind: string; card: Record<string, unknown> };
const NAMES = ["last_name", "first_name", "middle_name", "birth_date", "company_name"];

export function NewClientPage() {
  return <NewCardWizard cardType="client" />;
}

export function NewCounterpartyPage() {
  return <NewCardWizard cardType="counterparty" />;
}

/** An investor entered by hand: one who put in money has no vehicle to be entered with
 * (customer's decision of 26.11.2026). */
export function NewInvestorPage() {
  return <NewCardWizard cardType="investor" />;
}

/** Step-by-step creation of a client or a counterparty (customer's decision of 11.11.2026), as
 * the creation of a vehicle: the kind, the name, the birth date of a person, the contacts — «Add a
 * contact», the kind first, then the value (customer's remarks of 25.11.2026). The link with a
 * profile is asked from the card afterwards, by a contact or by an ID. The minimum of § 6.2.7–6.2.8 stays:
 * the first and the last name or the company name, and a phone or an email. */
function NewCardWizard({ cardType }: { cardType: CardType }) {
  const { t } = useTranslation();
  const company = useCompany();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const path = apiList(cardType);
  const cardPath = (id: string) => companyPath(company, pathOfCard(cardType, id));
  const back = companyPath(company, listPath(cardType));
  const [form, setForm] = useState<Form>({
    kind: cardType === "client" ? "person" : "company",
    first_name: "",
    last_name: "",
    middle_name: "",
    birth_date: "",
    company_name: "",
    contacts: [{ choice: "", value: "" }],
    comment: "",
  });
  const [index, setIndex] = useState(0);
  const set = (changes: Partial<Form>) => setForm({ ...form, ...changes });
  const text = (key: keyof Form) => (e: { target: { value: string } }) => set({ [key]: e.target.value });
  const create = useCommand<Record<string, unknown>, { client?: Client; counterparty?: Counterparty }>(
    `/c/${company.public_id}/${path}`,
  );
  // A photo of a passport, a licence or a paper of a company fills the card: the names go into
  // the steps, the rest is shown to be checked and is written with the card; the photo is kept
  // with the card (customer's decisions of 28.11.2026). Nothing is made until the last step.
  const able = useApi<{ available: boolean }>(
    ["card-recognition", company.public_id],
    `/c/${company.public_id}/cards/recognize`,
  );
  const reading = useCommand<{ document_id: string; card_type: CardType }, Reading>(
    `/c/${company.public_id}/cards/recognize`,
  );
  const [uploading, setUploading] = useState(false);
  const [refused, setRefused] = useState<ApiError | null>(null);
  const [paper, setPaper] = useState<{ file_id: string; kind: string } | null>(null);
  const [more, setMore] = useState<Record<string, unknown>>({});
  const [nothing, setNothing] = useState(false);
  async function readPaper(files: File[]) {
    setNothing(false);
    setRefused(null);
    setUploading(true);
    let id = "";
    try {
      id = (await uploadPages("party_paper", files)).id;
    } catch (error) {
      if (error instanceof ApiError) setRefused(error);
      return;
    } finally {
      setUploading(false);
    }
    const read = await reading.run({ document_id: id, card_type: cardType });
    if (read === null) return;
    const text = (key: string) => (typeof read.card[key] === "string" ? (read.card[key] as string) : "");
    const rest = Object.fromEntries(
      Object.entries(read.card).filter(([key, value]) => !NAMES.includes(key) && value !== "" && value !== null),
    );
    const named = NAMES.some((key) => text(key));
    setNothing(!named && !Object.keys(rest).length);
    setForm({
      ...form,
      kind: read.kind,
      first_name: text("first_name"),
      last_name: text("last_name"),
      middle_name: text("middle_name"),
      birth_date: text("birth_date"),
      company_name: text("company_name"),
    });
    setMore(rest);
    setPaper({ file_id: id, kind: read.document_kind });
    // To the names, to be checked.
    setIndex(1);
  }
  // Possible existing clients for the entered data (§ 6.1.11); the manager compares and decides.
  const matches = useMatches({ ...form, ...phoneAndEmail(form.contacts) }, cardType === "client");

  const steps: Step[] = [
    // No step of an ID: the card is made of the data; the link with a profile is asked from the
    // card afterwards (customer's remark of 25.11.2026).
    "kind",
    "name",
    ...(form.kind === "person" ? (["birth"] as Step[]) : []),
    "contacts",
  ];
  const step: Step = steps[Math.min(index, steps.length - 1)] ?? "kind";
  const last = step === "contacts";
  const named =
    form.kind === "person" ? Boolean(form.first_name.trim() && form.last_name.trim()) : Boolean(form.company_name.trim());
  const reachable = contactsOf(form.contacts).length > 0 && form.contacts.every(contactValid);
  const ready = step === "name" ? named : step === "contacts" ? reachable : true;
  const pending = create.pending;

  async function opened(result: { client?: Client; counterparty?: Counterparty } | null) {
    const card = result?.client ?? result?.counterparty;
    if (!card) return;
    await queryClient.invalidateQueries({ queryKey: [path, company.public_id] });
    navigate(cardPath(card.id));
  }

  async function save() {
    const contacts = contactsOf(form.contacts);
    const body: Record<string, unknown> =
      form.kind === "person"
        ? {
            kind: "person",
            first_name: form.first_name,
            last_name: form.last_name,
            middle_name: form.middle_name,
            contacts,
          }
        : { kind: "company", company_name: form.company_name, contacts };
    if (form.kind === "person") body.birth_date = form.birth_date || null;
    if (cardType !== "client") body.comment = form.comment;
    // What was read besides the names; of the kind of the card only.
    const person = ["id_document", "driver_license", "registration_address"];
    for (const [key, value] of Object.entries(more)) {
      if ((form.kind === "person") === person.includes(key)) body[key] = value;
    }
    if (paper) body.paper = paper;
    await opened(await create.run(body));
  }

  function next(event: FormEvent) {
    event.preventDefault();
    if (!ready || pending) return;
    if (last) void save();
    else setIndex(index + 1);
  }

  function skip() {
    if (step === "birth") set({ birth_date: "" });
    setIndex(index + 1);
  }

  const titles: Record<Step, string> = {
    kind: t("clients.kind"),
    name: form.kind === "person" ? t("clients.wizard_name_person") : t("clients.company_name"),
    birth: t("clients.birth_date"),
    contacts: t("clients.wizard_contacts"),
  };

  let content: ReactNode = null;
  if (step === "kind") {
    const kinds = cardType === "client" ? (["person", "company"] as const) : (["company", "person"] as const);
    content = (
      <div className="stack">
        {kinds.map((kind) => (
          <label key={kind} style={{ display: "block" }}>
            <input type="radio" name="kind" checked={form.kind === kind} onChange={() => set({ kind })} />{" "}
            {t(`clients.kind_${kind}`)}
          </label>
        ))}
      </div>
    );
  } else if (step === "name") {
    content =
      form.kind === "person" ? (
        <div className="stack">
          <div className="row">
            <Field label={t("profile.last_name")}>
              <input autoFocus value={form.last_name} onChange={text("last_name")} />
            </Field>
            <Field label={t("profile.first_name")}>
              <input value={form.first_name} onChange={text("first_name")} />
            </Field>
            <Field label={t("profile.middle_name")}>
              <input value={form.middle_name} onChange={text("middle_name")} />
            </Field>
          </div>
          <p className="muted small">{t("clients.wizard_name_required")}</p>
        </div>
      ) : (
        <div className="stack">
          <Field label={t("clients.company_name")}>
            <input autoFocus value={form.company_name} onChange={text("company_name")} />
          </Field>
          <p className="muted small">{t("clients.wizard_company_required")}</p>
        </div>
      );
  } else if (step === "birth") {
    content = (
      <Field label={t("clients.birth_date")}>
        <input autoFocus type="date" value={form.birth_date} onChange={text("birth_date")} />
      </Field>
    );
  } else {
    content = (
      <div className="stack">
        <ContactsEditor titled={false} rows={form.contacts} onChange={(contacts) => set({ contacts })} />
        {cardType !== "client" ? (
          <Field label={t("warehouse.comment")}>
            <input value={form.comment} onChange={text("comment")} />
          </Field>
        ) : null}
        <p className="muted small">{t("clients.wizard_contact_required")}</p>
        {(matches.data ?? []).length > 0 ? (
          <div className="alert warn">
            <strong>{t("clients.matches_title")}</strong>
            <ul>
              {(matches.data ?? []).map(({ client: match, matched }) => (
                <li key={match.id}>
                  <Link to={cardPath(match.id)}>{clientName(match)}</Link>{" "}
                  <span className="small">
                    ({matched.map((m) => t(`clients.matched_${m}`)).join(", ")};{" "}
                    {match.contacts.map((c) => c.value).join(", ")})
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        <ErrorMessage error={create.error} />
      </div>
    );
  }

  const optional = step === "birth";
  return (
    <div className="page">
      <p>
        <Link to={back}>← {t(`clients.tab_${path}`)}</Link>
      </p>
      <h1>{t(`${path}.create_title`)}</h1>
      <p className="muted">{t("wizard.step", { n: index + 1, total: steps.length })}</p>
      {able.data?.available ? (
        <div className="paper-reading">
          <label className={`button secondary${uploading || reading.pending ? " disabled" : ""}`}>
            {uploading || reading.pending ? t("vehicles.paper_reading") : t("vehicles.paper_read")}
            <input
              type="file"
              hidden
              multiple
              aria-label={t("vehicles.paper_read")}
              accept="image/png,image/jpeg,image/webp,application/pdf"
              disabled={uploading || reading.pending}
              onChange={(e) => {
                const files = [...(e.target.files ?? [])];
                e.target.value = "";
                if (files.length) void readPaper(files);
              }}
            />
          </label>
          <Info text={t("cards.paper_hint")} />
          {nothing ? (
            <span className="small" role="status">
              {t("vehicles.paper_nothing")}
            </span>
          ) : null}
          <ErrorMessage error={refused ?? reading.error} />
        </div>
      ) : null}
      {paper && !nothing ? (
        <div className="alert ok small" data-testid="card-read" style={{ maxWidth: 760 }}>
          <strong>{t("cards.paper_read_title", { document: t(`cards.paper_${paper.kind}`) })}</strong>
          <ReadFacts values={more} />
          <div>{t("cards.paper_read_check")}</div>
        </div>
      ) : null}
      <form onSubmit={next} key={step}>
        <Card title={titles[step]}>
          {content}
          {optional ? <p className="muted small">{t("wizard.optional")}</p> : null}
          <div className="actions">
            {index > 0 ? (
              <button type="button" className="secondary" onClick={() => setIndex(index - 1)}>
                {t("wizard.back")}
              </button>
            ) : null}
            {optional ? (
              <button type="button" className="secondary" onClick={skip}>
                {t("wizard.skip")}
              </button>
            ) : null}
            <button type="submit" disabled={!ready || pending}>
              {last ? t(`${path}.create`) : t("wizard.next")}
            </button>
          </div>
        </Card>
      </form>
    </div>
  );
}

/** What was read besides the names, in words: it is written with the card and is corrected in
 * the card afterwards. */
function ReadFacts({ values }: { values: Record<string, unknown> }) {
  const { t } = useTranslation();
  const lines: string[] = [];
  const paper = (label: string, value: unknown) => {
    if (!value || typeof value !== "object") return;
    const d = value as Record<string, unknown>;
    const parts = [
      d.number,
      d.country,
      d.issued_on ? `${t("cards.issued_on")}: ${String(d.issued_on)}` : "",
      d.expires_on ? `${t("cards.expires_on")}: ${String(d.expires_on)}` : "",
      d.issued_by ? `${t("cards.issued_by")}: ${String(d.issued_by)}` : "",
      Array.isArray(d.categories) && d.categories.length ? `${t("cards.categories_short")}: ${d.categories.join(", ")}` : "",
    ].filter(Boolean);
    lines.push(`${label}: ${parts.join(" · ")}`);
  };
  paper(t("cards.paper_passport"), values.id_document);
  paper(t("cards.driver_license"), values.driver_license);
  for (const key of ["registration_address", "legal_form", "registration_number", "tax_number", "country", "legal_address", "director_name"]) {
    if (typeof values[key] === "string" && values[key]) lines.push(`${t(`cards.field_${key}`)}: ${values[key] as string}`);
  }
  if (!lines.length) return null;
  return (
    <ul style={{ margin: "4px 0" }}>
      {lines.map((line) => (
        <li key={line}>{line}</li>
      ))}
    </ul>
  );
}
