import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router";

import type { Client, ClientContact, Counterparty, Messenger } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { clientName, MessengerField, useMatches } from "./ClientsPage";

type CardType = "client" | "counterparty";
type Step = "kind" | "link" | "name" | "birth" | "contacts";

type Form = {
  kind: "person" | "company";
  target: string;
  first_name: string;
  last_name: string;
  middle_name: string;
  birth_date: string;
  company_name: string;
  phone: string;
  phone_messenger: Messenger;
  email: string;
  comment: string;
};

export function NewClientPage() {
  return <NewCardWizard cardType="client" />;
}

export function NewCounterpartyPage() {
  return <NewCardWizard cardType="counterparty" />;
}

/** Step-by-step creation of a client or a counterparty (customer's decision of 11.11.2026), as
 * the creation of a vehicle: the kind; then, first of all, a request to a profile by its ID in
 * place of filling the card (§ 6.2.9), which may be skipped; then the name, the birth date of a
 * person, the contacts with the preferred way of contact. The minimum of § 6.2.7–6.2.8 stays:
 * the first and the last name or the company name, and a phone or an email. */
function NewCardWizard({ cardType }: { cardType: CardType }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const path = cardType === "client" ? "clients" : "counterparties";
  const cardPath = (id: string) =>
    companyPath(company, cardType === "client" ? `/clients/${id}` : `/clients/counterparties/${id}`);
  const back = companyPath(company, cardType === "client" ? "/clients" : "/clients?tab=counterparties");
  const [form, setForm] = useState<Form>({
    kind: cardType === "client" ? "person" : "company",
    target: "",
    first_name: "",
    last_name: "",
    middle_name: "",
    birth_date: "",
    company_name: "",
    phone: "",
    phone_messenger: "",
    email: "",
    comment: "",
  });
  const [index, setIndex] = useState(0);
  const set = (changes: Partial<Form>) => setForm({ ...form, ...changes });
  const text = (key: keyof Form) => (e: { target: { value: string } }) => set({ [key]: e.target.value });
  const create = useCommand<Record<string, unknown>, { client?: Client; counterparty?: Counterparty }>(
    `/c/${company.public_id}/${path}`,
  );
  const request = useCommand<{ kind: string; target: string }, { client?: Client; counterparty?: Counterparty }>(
    `/c/${company.public_id}/${path}/provisional`,
  );
  // Possible existing clients for the entered data (§ 6.1.11); the manager compares and decides.
  const matches = useMatches(form, cardType === "client");

  const steps: Step[] = [
    "kind",
    ...(can(`${cardType}.link.request`) ? (["link"] as Step[]) : []),
    "name",
    ...(cardType === "client" && form.kind === "person" ? (["birth"] as Step[]) : []),
    "contacts",
  ];
  const step: Step = steps[Math.min(index, steps.length - 1)] ?? "kind";
  const last = step === "contacts";
  const named =
    form.kind === "person" ? Boolean(form.first_name.trim() && form.last_name.trim()) : Boolean(form.company_name.trim());
  const reachable = Boolean(form.phone.trim() || form.email.trim());
  const ready = step === "name" ? named : step === "contacts" ? reachable : step === "link" ? Boolean(form.target.trim()) : true;
  const pending = create.pending || request.pending;

  async function opened(result: { client?: Client; counterparty?: Counterparty } | null) {
    const card = result?.client ?? result?.counterparty;
    if (!card) return;
    await queryClient.invalidateQueries({ queryKey: [path, company.public_id] });
    navigate(cardPath(card.id));
  }

  async function save() {
    const contacts: ClientContact[] = [];
    if (form.phone.trim()) contacts.push({ kind: "phone", value: form.phone, messenger: form.phone_messenger });
    if (form.email.trim()) contacts.push({ kind: "email", value: form.email });
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
    if (cardType === "client" && form.kind === "person") body.birth_date = form.birth_date || null;
    if (cardType === "counterparty") body.comment = form.comment;
    await opened(await create.run(body));
  }

  function next(event: FormEvent) {
    event.preventDefault();
    if (!ready || pending) return;
    // The request by the ID creates the card at once, in place of filling it.
    if (step === "link") void request.run({ kind: form.kind, target: form.target }).then(opened);
    else if (last) void save();
    else setIndex(index + 1);
  }

  function skip() {
    if (step === "link") set({ target: "" });
    if (step === "birth") set({ birth_date: "" });
    setIndex(index + 1);
  }

  const titles: Record<Step, string> = {
    kind: t("clients.kind"),
    link: t("cards.provisional_title"),
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
  } else if (step === "link") {
    content = (
      <div className="stack">
        <Field
          label={t(form.kind === "company" ? "links.target_company" : "links.target_person")}
          hint={t("cards.provisional_hint")}
        >
          <input autoFocus value={form.target} onChange={text("target")} />
        </Field>
        <ErrorMessage error={request.error} />
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
        <div className="row">
          <Field label={t("clients.phone")}>
            <input autoFocus type="tel" value={form.phone} onChange={text("phone")} />
          </Field>
          <MessengerField value={form.phone_messenger} onChange={(phone_messenger) => set({ phone_messenger })} />
          <Field label={t("auth.email")}>
            <input type="email" value={form.email} onChange={text("email")} />
          </Field>
        </div>
        {cardType === "counterparty" ? (
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

  const optional = step === "link" || step === "birth";
  return (
    <div className="page">
      <p>
        <Link to={back}>← {t(cardType === "client" ? "clients.tab_clients" : "clients.tab_counterparties")}</Link>
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
              {step === "link" ? t("cards.provisional_send") : last ? t(`${path}.create`) : t("wizard.next")}
            </button>
          </div>
        </Card>
      </form>
    </div>
  );
}
