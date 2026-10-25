import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams, useSearchParams } from "react-router";

import { api } from "../../api/client";
import { useApi } from "../../api/queries";
import type { Client, ClientContact } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { EmployeesList } from "../settings/EmployeesPage";
import { CounterpartiesList } from "./Counterparties";

export function clientName(client: Pick<Client, "kind" | "first_name" | "last_name" | "company_name">) {
  return client.kind === "company" ? client.company_name : `${client.first_name} ${client.last_name}`.trim();
}

/** Clients: list with search when allowed (§ 6.2.2), creation with possible matches (§ 6.1.11). */
type PeopleTab = "clients" | "counterparties" | "employees";

/** Tabs the employee may see (decision of 24.10.2026): each is shown only with its rights
 * (§ 23.8.2: clients and counterparties have rights of their own). */
export function peopleTabs(can: (permission: string) => boolean): PeopleTab[] {
  const tabs: PeopleTab[] = [];
  if (can("client.list") || can("client.create")) tabs.push("clients");
  if (can("counterparty.view") || can("counterparty.create")) tabs.push("counterparties");
  if (can("employee.invite") || can("employee.rights.manage") || can("employee.access.block")) {
    tabs.push("employees");
  }
  return tabs;
}

/** «Clients and counterparties» (decisions of 24.10.2026): one menu item, separate lists
 * (§ 6.1.7) on neighbouring tabs — clients, counterparties, employees. */
export function ClientsPage() {
  const { t } = useTranslation();
  const can = useCan();
  const [params, setParams] = useSearchParams();
  const tabs = peopleTabs(can);
  const wanted = params.get("tab") as PeopleTab | null;
  const tab = wanted && tabs.includes(wanted) ? wanted : tabs[0];
  if (!tab) {
    return (
      <div className="page">
        <h1>{t("nav.clients")}</h1>
        <p className="muted">{t("clients.nothing_allowed")}</p>
      </div>
    );
  }
  return (
    <div className="page">
      <h1>{t("nav.clients")}</h1>
      <div className="tabs" role="tablist">
        {tabs.map((key) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            className={tab === key ? "active" : ""}
            onClick={() => setParams(key === tabs[0] ? {} : { tab: key })}
          >
            {t(`clients.tab_${key}`)}
          </button>
        ))}
      </div>
      {tab === "clients" ? <ClientsList /> : null}
      {tab === "counterparties" ? <CounterpartiesList /> : null}
      {tab === "employees" ? <EmployeesList /> : null}
    </div>
  );
}

function ClientsList() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const [query, setQuery] = useState("");
  const list = useApi<{ clients: Client[] }>(
    ["clients", company.public_id, query],
    `/c/${company.public_id}/clients?q=${encodeURIComponent(query)}`,
    can("client.list"),
  );
  return (
    <>
      {can("client.list") ? (
        <Card>
          <Field label={t("clients.search")}>
            <input value={query} onChange={(e) => setQuery(e.target.value)} />
          </Field>
          {list.isLoading ? <Loading /> : null}
          <table>
            <tbody>
              {(list.data?.clients ?? []).map((client) => (
                <tr key={client.id}>
                  <td>
                    <Link to={companyPath(company, `/clients/${client.id}`)}>{clientName(client)}</Link>
                  </td>
                  <td className="small muted">{client.contacts.map((c) => c.value).join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      ) : (
        <p className="muted">{t("clients.list_not_allowed")}</p>
      )}
      {can("client.create") ? <ClientForm /> : null}
    </>
  );
}

type Draft = {
  kind: "person" | "company";
  first_name: string;
  last_name: string;
  middle_name: string;
  birth_date: string;
  company_name: string;
  phone: string;
  email: string;
};

const EMPTY: Draft = {
  kind: "person",
  first_name: "",
  last_name: "",
  middle_name: "",
  birth_date: "",
  company_name: "",
  phone: "",
  email: "",
};

/** Possible existing cards for entered data (§ 6.1.11); the manager compares and decides. */
export function useMatches(draft: Draft) {
  const company = useCompany();
  const can = useCan();
  const [debounced, setDebounced] = useState(draft);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(draft), 400);
    return () => window.clearTimeout(timer);
  }, [draft]);
  const filled = Boolean(
    (debounced.first_name && debounced.last_name) ||
      debounced.company_name ||
      debounced.phone.length >= 5 ||
      debounced.email.includes("@"),
  );
  return useQuery({
    queryKey: ["client-matches", company.public_id, debounced],
    enabled: filled && can("client.find"),
    retry: false,
    queryFn: async () => {
      const body: Record<string, string> = {};
      for (const key of ["first_name", "last_name", "birth_date", "company_name", "phone", "email"] as const) {
        if (debounced[key]) body[key] = debounced[key];
      }
      return (
        await api<{ matches: { client: Client; matched: string[] }[] }>(
          `/c/${company.public_id}/clients/matches`,
          { method: "POST", body },
        )
      ).matches;
    },
  });
}

export function ClientForm({ onCreated }: { onCreated?: (client: Client) => void }) {
  const { t } = useTranslation();
  const company = useCompany();
  const client = useQueryClient();
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const matches = useMatches(draft);
  const create = useCommand<Record<string, unknown>, { client: Client }>(`/c/${company.public_id}/clients`);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const contacts: ClientContact[] = [];
    if (draft.phone.trim()) contacts.push({ kind: "phone", value: draft.phone });
    if (draft.email.trim()) contacts.push({ kind: "email", value: draft.email });
    const body: Record<string, unknown> =
      draft.kind === "person"
        ? {
            kind: "person",
            first_name: draft.first_name,
            last_name: draft.last_name,
            middle_name: draft.middle_name,
            birth_date: draft.birth_date || null,
            contacts,
          }
        : { kind: "company", company_name: draft.company_name, contacts };
    const result = await create.run(body);
    if (result) {
      setDraft(EMPTY);
      await client.invalidateQueries({ queryKey: ["clients", company.public_id] });
      onCreated?.(result.client);
    }
  }

  const set = (key: keyof Draft) => (e: { target: { value: string } }) =>
    setDraft({ ...draft, [key]: e.target.value });

  return (
    <Card title={t("clients.create_title")}>
      <form className="stack" onSubmit={submit}>
        <Field label={t("clients.kind")}>
          <select value={draft.kind} onChange={set("kind")}>
            <option value="person">{t("clients.kind_person")}</option>
            <option value="company">{t("clients.kind_company")}</option>
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
            <Field label={t("clients.birth_date")}>
              <input type="date" value={draft.birth_date} onChange={set("birth_date")} />
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
          <Field label={t("auth.email")}>
            <input type="email" value={draft.email} onChange={set("email")} />
          </Field>
        </div>
        <p className="muted small">{t("clients.minimum")}</p>
        {(matches.data ?? []).length > 0 ? (
          <div className="alert warn">
            <strong>{t("clients.matches_title")}</strong>
            <ul>
              {(matches.data ?? []).map(({ client: match, matched }) => (
                <li key={match.id}>
                  {onCreated ? (
                    <button type="button" className="secondary" onClick={() => onCreated(match)}>
                      {t("clients.use_existing", { name: clientName(match) })}
                    </button>
                  ) : (
                    <Link to={companyPath(company, `/clients/${match.id}`)}>{clientName(match)}</Link>
                  )}{" "}
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
        <div className="actions">
          <button type="submit" disabled={create.pending}>
            {t("clients.create")}
          </button>
        </div>
      </form>
    </Card>
  );
}

/** Client card: data, contacts, settlement balance (§ 6.2.12, § 6.7.2). */
export function ClientPage() {
  const { t } = useTranslation();
  const { id = "" } = useParams();
  const company = useCompany();
  const can = useCan();
  const queryClient = useQueryClient();
  const card = useApi<{ client: Client }>(["client", company.public_id, id], `/c/${company.public_id}/clients/${id}`);
  const balance = useApi<{ balance: Record<string, string> }>(
    ["client-balance", company.public_id, id],
    `/c/${company.public_id}/clients/${id}/balance`,
  );
  const update = useCommand<Record<string, unknown>, unknown>(`/c/${company.public_id}/clients/${id}`, "PATCH");
  const [form, setForm] = useState<Record<string, string> | null>(null);
  if (card.isLoading) return <div className="page"><Loading /></div>;
  const data = card.data?.client;
  if (!data) return <div className="page"><ErrorMessage error={null} /></div>;
  const values = form ?? {
    first_name: data.first_name,
    last_name: data.last_name,
    middle_name: data.middle_name,
    company_name: data.company_name,
    phone: data.contacts.find((c) => c.kind === "phone")?.value ?? "",
    email: data.contacts.find((c) => c.kind === "email")?.value ?? "",
  };

  const current = data;
  async function save(event: FormEvent) {
    event.preventDefault();
    const contacts: ClientContact[] = [];
    if (values.phone?.trim()) contacts.push({ kind: "phone", value: values.phone });
    if (values.email?.trim()) contacts.push({ kind: "email", value: values.email });
    const body: Record<string, unknown> =
      current.kind === "person"
        ? {
            first_name: values.first_name,
            last_name: values.last_name,
            middle_name: values.middle_name,
            contacts,
          }
        : { company_name: values.company_name, contacts };
    if ((await update.run({ ...body, expected_version: current.version })) !== null) {
      setForm(null);
      await queryClient.invalidateQueries({ queryKey: ["client", company.public_id, id] });
    }
  }

  const field = (key: string, label: string) => (
    <Field label={label}>
      <input
        value={values[key] ?? ""}
        disabled={!can("client.edit")}
        onChange={(e) => setForm({ ...values, [key]: e.target.value })}
      />
    </Field>
  );

  return (
    <div className="page">
      <h1>{clientName(data)}</h1>
      <Card title={t("clients.balance")}>
        {Object.entries(balance.data?.balance ?? {}).length === 0 ? (
          <p className="muted">0</p>
        ) : (
          Object.entries(balance.data?.balance ?? {}).map(([currency, value]) => (
            <p key={currency}>
              {Number(value) < 0
                ? t("clients.debt", { amount: -Number(value), currency })
                : t("clients.advance", { amount: Number(value), currency })}
            </p>
          ))
        )}
      </Card>
      <Card>
        <form className="stack" onSubmit={save}>
          {data.kind === "person" ? (
            <div className="row">
              {field("first_name", t("profile.first_name"))}
              {field("last_name", t("profile.last_name"))}
              {field("middle_name", t("profile.middle_name"))}
            </div>
          ) : (
            field("company_name", t("clients.company_name"))
          )}
          {data.birth_date ? (
            <p className="small">
              {t("clients.birth_date")}: {data.birth_date}
            </p>
          ) : null}
          <div className="row">
            {field("phone", t("clients.phone"))}
            {field("email", t("auth.email"))}
          </div>
          <ErrorMessage error={update.error} />
          {can("client.edit") ? (
            <div className="actions">
              <button type="submit" disabled={!form || update.pending}>
                {t("common.save")}
              </button>
            </div>
          ) : null}
        </form>
      </Card>
    </div>
  );
}
