import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router";
import { ProfileLinkCard } from "./ProfileLinkCard";

import { useApi } from "../../api/queries";
import type { ClientContact, Counterparty, Messenger } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { contactText, extraContacts, MessengerField } from "./ClientsPage";

type Texts = { registration: string; address: string; category: string; external_id: string };

type Draft = {
  kind: "person" | "company";
  first_name: string;
  last_name: string;
  middle_name: string;
  company_name: string;
  phone: string;
  phone_messenger: Messenger;
  email: string;
  comment: string;
};

const EMPTY: Draft = {
  kind: "company",
  first_name: "",
  last_name: "",
  middle_name: "",
  company_name: "",
  phone: "",
  phone_messenger: "",
  email: "",
  comment: "",
};

function contactsOf(draft: Pick<Draft, "phone" | "phone_messenger" | "email">): ClientContact[] {
  const contacts: ClientContact[] = [];
  if (draft.phone.trim()) contacts.push({ kind: "phone", value: draft.phone, messenger: draft.phone_messenger });
  if (draft.email.trim()) contacts.push({ kind: "email", value: draft.email });
  return contacts;
}

/** Counterparties — suppliers of the company (§ 6.1.4), a list of its own apart from clients
 * (§ 6.1.7). The minimal list of the warehouse stage (customer's decision of 25.10.2026). */
export function CounterpartiesList() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const [query, setQuery] = useState("");
  const [archived, setArchived] = useState(false);
  const list = useApi<{ counterparties: Counterparty[] }>(
    ["counterparties", company.public_id, query, archived],
    `/c/${company.public_id}/counterparties?q=${encodeURIComponent(query)}&state=${archived ? "archived" : "active"}`,
    can("counterparty.view"),
  );
  return (
    <>
      {can("counterparty.view") ? (
        <Card>
          <div className="row">
            <Field label={t("counterparties.search")}>
              <input value={query} onChange={(e) => setQuery(e.target.value)} />
            </Field>
            <label style={{ alignSelf: "end" }}>
              <input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} />{" "}
              {t("counterparties.show_archived")}
            </label>
          </div>
          {list.isLoading ? <Loading /> : null}
          {list.data?.counterparties.length === 0 ? <p className="muted">{t("counterparties.none")}</p> : null}
          <table>
            <tbody>
              {(list.data?.counterparties ?? []).map((card) => (
                <tr key={card.id}>
                  <td>
                    <Link to={companyPath(company, `/clients/counterparties/${card.id}`)}>{card.name}</Link>
                  </td>
                  <td>{t(`clients.kind_${card.kind}`)}</td>
                  <td className="small muted">{card.contacts.map((c) => c.value).join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      ) : (
        <p className="muted">{t("counterparties.list_not_allowed")}</p>
      )}
      {can("counterparty.create") ? <CounterpartyForm /> : null}
    </>
  );
}

/** A new counterparty: person — first and last name and a contact; company — name and a contact
 * (§ 6.2.7–6.2.8). Also used right at a receipt (§ 11.2.8). */
export function CounterpartyForm({ onCreated }: { onCreated?: (card: Counterparty) => void }) {
  const { t } = useTranslation();
  const company = useCompany();
  const client = useQueryClient();
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const create = useCommand<Record<string, unknown>, { counterparty: Counterparty }>(
    `/c/${company.public_id}/counterparties`,
  );

  async function submit(event: FormEvent) {
    event.preventDefault();
    event.stopPropagation();
    const contacts = contactsOf(draft);
    const body: Record<string, unknown> =
      draft.kind === "person"
        ? {
            kind: "person",
            first_name: draft.first_name,
            last_name: draft.last_name,
            middle_name: draft.middle_name,
            comment: draft.comment,
            contacts,
          }
        : { kind: "company", company_name: draft.company_name, comment: draft.comment, contacts };
    const result = await create.run(body);
    if (result) {
      setDraft(EMPTY);
      await client.invalidateQueries({ queryKey: ["counterparties", company.public_id] });
      onCreated?.(result.counterparty);
    }
  }

  const set = (key: keyof Draft) => (e: { target: { value: string } }) => setDraft({ ...draft, [key]: e.target.value });

  return (
    <Card title={t("counterparties.create_title")}>
      <form className="stack" onSubmit={submit}>
        <Field label={t("clients.kind")}>
          <select value={draft.kind} onChange={set("kind")}>
            <option value="company">{t("clients.kind_company")}</option>
            <option value="person">{t("clients.kind_person")}</option>
          </select>
        </Field>
        {draft.kind === "person" ? (
          <div className="row">
            <Field label={t("profile.first_name")}>
              <input value={draft.first_name} onChange={set("first_name")} />
            </Field>
            <Field label={t("profile.last_name")}>
              <input value={draft.last_name} onChange={set("last_name")} />
            </Field>
            <Field label={t("profile.middle_name")}>
              <input value={draft.middle_name} onChange={set("middle_name")} />
            </Field>
          </div>
        ) : (
          <Field label={t("clients.company_name")}>
            <input value={draft.company_name} onChange={set("company_name")} />
          </Field>
        )}
        <div className="row">
          <Field label={t("clients.phone")}>
            <input type="tel" value={draft.phone} onChange={set("phone")} />
          </Field>
          <MessengerField
            value={draft.phone_messenger}
            onChange={(messenger) => setDraft({ ...draft, phone_messenger: messenger })}
          />
          <Field label={t("auth.email")}>
            <input type="email" value={draft.email} onChange={set("email")} />
          </Field>
        </div>
        <Field label={t("warehouse.comment")}>
          <input value={draft.comment} onChange={set("comment")} />
        </Field>
        <p className="muted small">{t("clients.minimum")}</p>
        <ErrorMessage error={create.error} />
        <div className="actions">
          <button type="submit" disabled={create.pending}>
            {t("counterparties.create")}
          </button>
        </div>
      </form>
    </Card>
  );
}

/** Card of a counterparty: data, contacts, archive and restore (§ 6.6.4–6.6.5). */
export function CounterpartyPage() {
  const { t } = useTranslation();
  const { id = "" } = useParams();
  const company = useCompany();
  const can = useCan();
  const queryClient = useQueryClient();
  const base = `/c/${company.public_id}/counterparties/${id}`;
  const card = useApi<{ counterparty: Counterparty }>(["counterparty", company.public_id, id], base);
  const update = useCommand<Record<string, unknown>, unknown>(base, "PATCH");
  const archive = useCommand<{ expected_version: number }, unknown>(`${base}/archive`);
  const restore = useCommand<{ expected_version: number }, unknown>(`${base}/restore`);
  const [form, setForm] = useState<(Draft & { texts?: Texts }) | null>(null);
  if (card.isLoading) return <div className="page"><Loading /></div>;
  const data = card.data?.counterparty;
  if (!data) return <div className="page"><ErrorMessage error={null} /></div>;
  const current = data;
  const values: Draft = form ?? {
    kind: data.kind,
    first_name: data.first_name,
    last_name: data.last_name,
    middle_name: data.middle_name,
    company_name: data.company_name,
    phone: data.contacts.find((c) => c.kind === "phone")?.value ?? "",
    phone_messenger: data.contacts.find((c) => c.kind === "phone")?.messenger ?? "",
    email: data.contacts.find((c) => c.kind === "email")?.value ?? "",
    comment: data.comment,
  };
  const texts = form?.texts ?? {
    registration: data.registration,
    address: data.address,
    category: data.category,
    external_id: data.external_id,
  };
  const editable = can("counterparty.edit") && data.state === "active";

  async function refresh() {
    setForm(null);
    await queryClient.invalidateQueries({ queryKey: ["counterparty", company.public_id, id] });
    await queryClient.invalidateQueries({ queryKey: ["counterparties", company.public_id] });
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    const names =
      current.kind === "person"
        ? { first_name: values.first_name, last_name: values.last_name, middle_name: values.middle_name }
        : { company_name: values.company_name };
    const body = {
      ...names,
      ...texts,
      comment: values.comment,
      // Further phones and emails (from an import) are kept as they are.
      contacts: [...contactsOf(values), ...extraContacts(current.contacts)],
      expected_version: current.version,
    };
    if ((await update.run(body)) !== null) await refresh();
  }

  const field = (key: keyof Draft, label: string) => (
    <Field label={label}>
      <input
        value={values[key]}
        disabled={!editable}
        onChange={(e) => setForm({ ...values, [key]: e.target.value, texts })}
      />
    </Field>
  );
  const text = (key: keyof typeof texts, label: string) => (
    <Field label={label}>
      <input
        value={texts[key]}
        disabled={!editable}
        onChange={(e) => setForm({ ...values, texts: { ...texts, [key]: e.target.value } })}
      />
    </Field>
  );

  return (
    <div className="page">
      <p>
        <Link to={companyPath(company, "/clients?tab=counterparties")}>← {t("clients.tab_counterparties")}</Link>
      </p>
      <h1>
        {data.name}{" "}
        {data.state === "archived" ? <span className="badge warn">{t("counterparties.archived")}</span> : null}{" "}
        {data.possible_duplicate ? <span className="badge warn">{t("imports.possible_duplicate")}</span> : null}
      </h1>
      <Card>
        <form className="stack" onSubmit={save}>
          <p className="small muted">{t(`clients.kind_${data.kind}`)}</p>
          {data.kind === "person" ? (
            <div className="row">
              {field("first_name", t("profile.first_name"))}
              {field("last_name", t("profile.last_name"))}
              {field("middle_name", t("profile.middle_name"))}
            </div>
          ) : (
            field("company_name", t("clients.company_name"))
          )}
          <div className="row">
            {field("phone", t("clients.phone"))}
            <MessengerField
              value={values.phone_messenger}
              disabled={!editable}
              onChange={(messenger) => setForm({ ...values, phone_messenger: messenger, texts })}
            />
            {field("email", t("auth.email"))}
          </div>
          {extraContacts(data.contacts).map((c) => (
            <p key={`${c.kind}-${c.value}`} className="small">
              {contactText(t, c)}
            </p>
          ))}
          {field("comment", t("warehouse.comment"))}
          {text("registration", t("imports.registration"))}
          <div className="row">
            {text("address", t("imports.address"))}
            {text("category", t("imports.category"))}
            {text("external_id", t("imports.external_id"))}
          </div>
          <ErrorMessage error={update.error ?? archive.error ?? restore.error} />
          <div className="actions">
            {editable ? (
              <button type="submit" disabled={!form || update.pending}>
                {t("common.save")}
              </button>
            ) : null}
            {data.state === "active" && can("counterparty.archive") ? (
              <button
                type="button"
                className="secondary"
                disabled={archive.pending}
                onClick={async () => {
                  if ((await archive.run({ expected_version: current.version })) !== null) await refresh();
                }}
              >
                {t("warehouse.archive")}
              </button>
            ) : null}
            {data.state === "archived" && can("counterparty.restore") ? (
              <button
                type="button"
                className="secondary"
                disabled={restore.pending}
                onClick={async () => {
                  if ((await restore.run({ expected_version: current.version })) !== null) await refresh();
                }}
              >
                {t("warehouse.restore")}
              </button>
            ) : null}
          </div>
        </form>
      </Card>
      <ProfileLinkCard
        cardType="counterparty"
        cardId={data.id}
        cardKind={data.kind}
        version={data.version}
        onChanged={refresh}
      />
    </div>
  );
}
