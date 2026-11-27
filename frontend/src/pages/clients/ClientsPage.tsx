import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router";

import { api } from "../../api/client";
import { useApi } from "../../api/queries";
import { ProfileLinkCard } from "./ProfileLinkCard";
import { CardSettings } from "./CardSettings";
import { ClientGroupsCard } from "../orders/Discounts";
import type { CardDuplicate, Client, ClientContact } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Icon } from "../../components/icons";
import { Card, ErrorMessage, Field, IconButton, Loading, Modal } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { CompanyTabs } from "../../layout/CompanyTabs";
import { SectionTabs } from "../../layout/SectionTabs";
import { useFirstTab } from "../../layout/tabOrder";
import { RowTags, TagFilter, TagsLine, useTagsOf } from "../tags/Tags";
import { PersonRoles } from "./PersonRoles";
import { EmployeesList, InviteForm } from "../settings/EmployeesPage";
import { cardPath, type CardType } from "./cardLists";
import { CounterpartiesList } from "./Counterparties";
import { LinkedDocuments } from "../documents/DocumentsPage";
import {
  ArchiveActions,
  ClientMoneyCard,
  MergeCard,
  RestrictionsCard,
} from "./CardExtras";
import { ClientBalanceCard, ClientInteractionsCard, DocumentsCard, Facts, MainCard } from "./CardParts";
import { ContactsEditor, contactLabel, contactsOf, phoneAndEmail, type ContactRow } from "./ContactsEditor";

export function clientName(client: Pick<Client, "kind" | "first_name" | "last_name" | "company_name">) {
  return client.kind === "company" ? client.company_name : `${client.first_name} ${client.last_name}`.trim();
}

/** The state marks of a card: archive, blacklist (§ 6.7.1), provisional (§ 6.2.9), merged. */
export function CardBadges({
  card,
  cardType,
}: {
  card: {
    state: string;
    provisional: boolean;
    blacklisted?: boolean;
    possible_duplicate?: boolean;
    duplicates?: CardDuplicate[];
  };
  /** In the card itself: the mark of a possible duplicate opens with whom it matches. */
  cardType?: CardType;
}) {
  const { t } = useTranslation();
  return (
    <>
      {card.state !== "active" ? <span className="badge">{t(`cards.state_${card.state}`)}</span> : null}{" "}
      {card.blacklisted ? <span className="badge error">{t("cards.blacklisted")}</span> : null}{" "}
      {card.provisional ? <span className="badge warn">{t("cards.provisional")}</span> : null}{" "}
      {card.possible_duplicate ? (
        cardType && card.duplicates ? (
          <DuplicateBadge cardType={cardType} duplicates={card.duplicates} />
        ) : (
          <span className="badge warn">{t("imports.possible_duplicate")}</span>
        )
      ) : null}
    </>
  );
}

/** The mark «Possible duplicate» opens the cards of the same list the card matches and by what
 * (customer's remark of 25.11.2026). */
function DuplicateBadge({ cardType, duplicates }: { cardType: CardType; duplicates: CardDuplicate[] }) {
  const { t } = useTranslation();
  const company = useCompany();
  const [open, setOpen] = useState(false);
  return (
    <span style={{ position: "relative", display: "inline-block" }}>
      <button
        type="button"
        className="badge warn"
        style={{ cursor: "pointer", border: "none", font: "inherit", fontSize: "0.8rem", padding: "2px 8px", borderRadius: 999, background: "var(--warn-bg)", color: "var(--text)" }}
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        {t("imports.possible_duplicate")} ▾
      </button>
      {open ? (
        <div className="popup" data-testid="duplicates" style={{ position: "absolute", top: "100%", left: 0, zIndex: 20, minWidth: 320, padding: 12 }}>
          {duplicates.length ? (
            <>
              <div className="small muted">{t(`cards.duplicates_${cardType}`)}</div>
              <ul className="small" style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                {duplicates.map((d) => (
                  <li key={d.id}>
                    <Link to={companyPath(company, cardPath(cardType, d.id))}>
                      {d.name}
                    </Link>{" "}
                    <span className="muted">
                      ({t(`clients.kind_${d.kind}`)}; {t("cards.duplicates_by")} {d.matched.map((m) => t(`clients.matched_${m}`)).join(", ")})
                    </span>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <div className="small muted">{t("cards.duplicates_none")}</div>
          )}
        </div>
      ) : null}
    </span>
  );
}

/** Clients: list with search when allowed (§ 6.2.2), creation with possible matches (§ 6.1.11). */
type PeopleTab = "clients" | "counterparties" | "investors" | "employees";

/** Tabs the employee may see (decision of 24.10.2026): each is shown only with its rights
 * (§ 23.8.2: clients and counterparties have rights of their own). */
export function peopleTabs(can: (permission: string) => boolean): PeopleTab[] {
  const tabs: PeopleTab[] = [];
  if (can("client.list") || can("client.create")) tabs.push("clients");
  if (can("counterparty.view") || can("counterparty.create")) tabs.push("counterparties");
  // The investors are a list of its own, open not to everybody (decision of 26.11.2026).
  if (can("investor.view") || can("investor.create")) tabs.push("investors");
  if (can("employee.invite") || can("employee.rights.manage") || can("employee.access.block")) {
    tabs.push("employees");
  }
  return tabs;
}

/** «Clients and counterparties» (decisions of 24.10.2026): one menu item, separate lists
 * (§ 6.1.7) on neighbouring tabs — clients, counterparties, employees. */
export function ClientsPage() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const [params, setParams] = useSearchParams();
  const tabs = peopleTabs(can);
  const [inviting, setInviting] = useState(false);
  const firstTab = useFirstTab("contacts");
  const wanted = params.get("tab") as PeopleTab | null;
  const tab = wanted && tabs.includes(wanted) ? wanted : firstTab(tabs);
  if (!tab) {
    return (
      <div className="page full">
        <CompanyTabs current="contacts" />
        <h1>{t("nav.clients")}</h1>
        <p className="muted">{t("clients.nothing_allowed")}</p>
      </div>
    );
  }
  // The actions of the list stand in a column at the right edge, as in the garage (decision of
  // 11.11.2026); the cards are created step by step on their own pages.
  const actions: [boolean, string, string][] =
    tab === "clients"
      ? [[can("client.create"), "/clients/new", t("clients.new_button")]]
      : tab === "counterparties"
        ? [[can("counterparty.create"), "/clients/counterparties/new", t("counterparties.new_button")]]
        : tab === "investors"
          ? [[can("investor.create"), "/clients/investors/new", t("investors.new_button")]]
          : // Not a path: the invitation opens in a window over the list.
          [[can("employee.invite"), "invite", t("employees.new_button")]];
  const allowed = actions.filter(([may]) => may);
  return (
    <div className="page full">
      <h1 className="sr-only">{t("nav.clients")}</h1>
      <CompanyTabs current="contacts" />
      <div className={allowed.length ? "split" : ""}>
        <div>
      <SectionTabs
        section="contacts"
        current={tab}
        tabs={tabs.map((key) => ({ key, text: t(`clients.tab_${key}`) }))}
        onSelect={(key) => setParams({ tab: key })}
      />
      {tab === "clients" ? <ClientsList /> : null}
      {tab === "counterparties" ? <CounterpartiesList /> : null}
      {tab === "investors" ? <CounterpartiesList list="investor" /> : null}
      {tab === "employees" ? <EmployeesList /> : null}
        </div>
        {allowed.length ? (
          <aside className="side-actions" aria-label={t("vehicles.actions_title")}>
            {allowed.map(([, path, label]) =>
              path.startsWith("/") ? (
                <Link key={path} className="button secondary" to={companyPath(company, path)}>
                  <Icon name="plus" size={16} /> {label}
                </Link>
              ) : (
                <button key={path} type="button" className="secondary" onClick={() => setInviting(true)}>
                  <Icon name="plus" size={16} /> {label}
                </button>
              ),
            )}
          </aside>
        ) : null}
      </div>
      {inviting ? (
        <Modal wide title={t("employees.new_button")} onClose={() => setInviting(false)}>
          <InviteForm onClose={() => setInviting(false)} />
        </Modal>
      ) : null}
    </div>
  );
}

function ClientsList() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const [query, setQuery] = useState("");
  const [archived, setArchived] = useState(false);
  // The list is filtered by tags, and the tags stand in its rows (decisions of 28.11.2026).
  const [tags, setTags] = useState<string[]>([]);
  const tagsOf = useTagsOf("client");
  const list = useApi<{ clients: Client[] }>(
    ["clients", company.public_id, query, archived, tags.join(",")],
    `/c/${company.public_id}/clients?q=${encodeURIComponent(query)}&state=${archived ? "archived" : "active"}&tags=${tags.join(",")}`,
    can("client.list"),
  );
  return (
    <>
      {can("client.list") ? (
        <Card>
          <div className="list-bar">
            <input
              type="search"
              className="list-search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("clients.search")}
              aria-label={t("clients.search")}
            />
            <TagFilter kind="client" chosen={tags} onChange={setTags} />
            {/* § 6.6.4: archived cards are found with the «Archive» filter. */}
            <label>
              <input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} />{" "}
              {t("cards.show_archived")}
            </label>
          </div>
          {list.isLoading ? <Loading /> : null}
          <table>
            <tbody>
              {(list.data?.clients ?? []).map((client) => (
                <tr key={client.id}>
                  <td>
                    <Link to={companyPath(company, `/clients/${client.id}`)}>{client.name || clientName(client)}</Link>{" "}
                    <CardBadges card={client} /> <RowTags tags={tagsOf(client.id)} />
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
  contacts: ContactRow[];
};

const EMPTY: Draft = {
  kind: "person",
  first_name: "",
  last_name: "",
  middle_name: "",
  birth_date: "",
  company_name: "",
  contacts: [{ choice: "", value: "" }],
};

/** Possible existing cards for entered data (§ 6.1.11); the manager compares and decides. */
export function useMatches(
  draft: Pick<Draft, "first_name" | "last_name" | "birth_date" | "company_name"> & { phone: string; email: string },
  enabled = true,
) {
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
    enabled: enabled && filled && can("client.find"),
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
  const matches = useMatches({ ...draft, ...phoneAndEmail(draft.contacts) });
  const create = useCommand<Record<string, unknown>, { client: Client }>(`/c/${company.public_id}/clients`);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const contacts = contactsOf(draft.contacts);
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
    <Card>
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
        <ContactsEditor rows={draft.contacts} onChange={(contacts) => setDraft({ ...draft, contacts })} />
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
            {t("common.create")}
          </button>
        </div>
      </form>
    </Card>
  );
}

/** Client card: data, contacts, settlement balance (§ 6.2.12, § 6.7.2). */

/** A contact as text, with the messenger of a phone. */
export function contactText(t: (key: string) => string, c: ClientContact): string {
  return `${contactLabel(t, c)}: ${c.value}`;
}

/** Contacts beyond the first phone and the first email, which the form edits. */
export function extraContacts(contacts: ClientContact[]): ClientContact[] {
  const firstPhone = contacts.find((c) => c.kind === "phone");
  const firstEmail = contacts.find((c) => c.kind === "email");
  return contacts.filter((c) => c !== firstPhone && c !== firstEmail);
}

const CLIENT_TABS = ["main", "finance", "history", "settings"] as const;

/** The card of a client (customer's decisions of 15.11.2026): it looks like «About the company»
 * — the filled data as text, edited in a window by the pencil — with the tabs «Main», «Finance»,
 * «History» and «Settings» (the former «More»; customer's decision of 26.11.2026). */
export function ClientPage() {
  const { t } = useTranslation();
  const { id = "" } = useParams();
  const company = useCompany();
  const navigate = useNavigate();
  const route = useLocation();
  const queryClient = useQueryClient();
  const card = useApi<{ client: Client }>(["client", company.public_id, id], `/c/${company.public_id}/clients/${id}`);
  const [params, setParams] = useSearchParams();
  const firstTab = useFirstTab("client_card");
  if (card.isLoading) return <div className="page full"><Loading /></div>;
  const data = card.data?.client;
  if (!data) return <div className="page full"><ErrorMessage error={null} /></div>;
  const tab = CLIENT_TABS.find((x) => x === params.get("tab")) ?? firstTab(CLIENT_TABS) ?? "main";
  async function reload() {
    await queryClient.invalidateQueries({ queryKey: ["client", company.public_id, id] });
    await queryClient.invalidateQueries({ queryKey: ["clients", company.public_id] });
    await queryClient.invalidateQueries({ queryKey: ["client-history", company.public_id, id] });
  }
  return (
    <div className="page full">
      {/* The button «Back» stands before the tabs, outside their list. */}
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <IconButton
          icon="back"
          label={t("common.back")}
          onClick={() => (route.key === "default" ? navigate(companyPath(company, "/clients?tab=clients")) : navigate(-1))}
        />
        <SectionTabs
          section="client_card"
          current={tab}
          tabs={CLIENT_TABS.map((key) => ({ key, text: t(`cards.tab_${key}`) }))}
          onSelect={(key) => setParams({ tab: key }, { replace: true })}
          style={{ flex: 1 }}
        />
      </div>
      <TagsLine kind="client" id={data.id} />
      <PersonRoles cardType="client" id={data.id} />
      {data.merged_into ? (
        // § 6.5.2: the duplicate leads to the main card, where new operations go.
        <div className="alert warn">
          {t("cards.merged_banner")}{" "}
          <Link to={companyPath(company, `/clients/${data.merged_into}`)}>{t("cards.open_main")}</Link>
        </div>
      ) : null}
      {data.provisional ? <div className="alert warn">{t("cards.provisional_banner")}</div> : null}
      {data.blacklisted ? <div className="alert error">{t("cards.blacklisted_banner")}</div> : null}
      {tab === "main" ? (
        <>
          <MainCard cardType="client" card={data} badges={<CardBadges card={data} cardType="client" />} onSaved={reload} />
          <DocumentsCard cardType="client" card={data} onSaved={reload} />
        </>
      ) : null}
      {tab === "finance" ? (
        <>
          <ClientBalanceCard clientId={data.id} />
          <ClientMoneyCard clientId={data.id} />
        </>
      ) : null}
      {tab === "history" ? <ClientInteractionsCard clientId={data.id} /> : null}
      {tab === "settings" ? (
        <CardSettings
          section="client_settings"
          access={
            <div style={{ maxWidth: 760 }}>
              <ProfileLinkCard cardType="client" cardId={data.id} cardKind={data.kind} version={data.version} onChanged={reload} />
            </div>
          }
        >
          {/* Contracts, acts and other files of the client — not its personal documents. */}
          <LinkedDocuments objectType="client" objectId={data.id} />
          {data.state !== "merged" ? <RestrictionsCard clientId={data.id} onChanged={reload} /> : null}
          <ClientGroupsCard clientId={data.id} />
          <MergeCard cardType="client" card={data} onChanged={reload} />
          {data.external_id ? (
            <Card title={t("cards.other_title")}>
              <Facts rows={[[t("cards.field_external_id"), data.external_id]]} />
              <p className="muted small">{t("cards.external_id_hint")}</p>
            </Card>
          ) : null}
          {data.state !== "merged" ? (
            <Card>
              <ArchiveActions card={data} onChanged={reload} />
            </Card>
          ) : null}
        </CardSettings>
      ) : null}
    </div>
  );
}
