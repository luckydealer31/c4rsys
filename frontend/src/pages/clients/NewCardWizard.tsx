import { apiList, cardPath as pathOfCard, listPath, type CardType } from "./cardLists";
import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router";

import type { Client, Counterparty } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field } from "../../components/ui";
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
