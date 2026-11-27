import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router";
import { ProfileLinkCard } from "./ProfileLinkCard";
import { CardSettings } from "./CardSettings";
import { apiList, cardPath, listPath, type PartyList } from "./cardLists";

import { useApi } from "../../api/queries";
import type { Counterparty, Vehicle } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, IconButton, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { SectionTabs } from "../../layout/SectionTabs";
import { useFirstTab } from "../../layout/tabOrder";
import { RowTags, TagFilter, TagsLine, useTagsOf } from "../tags/Tags";
import { CardBadges } from "./ClientsPage";
import { PersonRoles } from "./PersonRoles";
import { ContactsEditor, contactsOf, type ContactRow } from "./ContactsEditor";
import { ContractorHistoryCard, MergeCard } from "./CardExtras";
import { DocumentsCard, Facts, MainCard, SupplierHistoryCard } from "./CardParts";
import { LinkedDocuments } from "../documents/DocumentsPage";
import { SupplierCard } from "../finance/Settlements";
import { EndManagementModal } from "../vehicles/VehicleLifecycle";

type Draft = {
  kind: "person" | "company";
  first_name: string;
  last_name: string;
  middle_name: string;
  birth_date: string;
  company_name: string;
  contacts: ContactRow[];
  comment: string;
};

const EMPTY: Draft = {
  kind: "company",
  first_name: "",
  last_name: "",
  middle_name: "",
  birth_date: "",
  company_name: "",
  contacts: [{ choice: "", value: "" }],
  comment: "",
};


/** Counterparties — suppliers of the company (§ 6.1.4), a list of its own apart from clients
 * (§ 6.1.7). The minimal list of the warehouse stage (customer's decision of 25.10.2026). */
export function CounterpartiesList({ list: of = "counterparty" }: { list?: PartyList }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  // The investors are a list of its own on the same cards (decision of 26.11.2026); the
  // categories are of the suppliers and are not offered there.
  const api = apiList(of);
  const investors = of === "investor";
  const [query, setQuery] = useState("");
  const [archived, setArchived] = useState(false);
  // The list is filtered by tags; the category of a counterparty is a tag now (customer's
  // decisions of 28.11.2026).
  const [tags, setTags] = useState<string[]>([]);
  const kind = investors ? "investor" : "counterparty";
  const tagsOf = useTagsOf(kind);
  const list = useApi<{ counterparties: Counterparty[] }>(
    [api, company.public_id, query, archived, tags.join(",")],
    `/c/${company.public_id}/${api}?q=${encodeURIComponent(query)}&state=${archived ? "archived" : "active"}&tags=${tags.join(",")}`,
    can(`${of}.view`),
  );
  return (
    <>
      {can(`${of}.view`) ? (
        <Card>
          <div className="list-bar">
            <input
              type="search"
              className="list-search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("counterparties.search")}
              aria-label={t("counterparties.search")}
              data-testid={`${api}-search`}
            />
            <TagFilter kind={kind} chosen={tags} onChange={setTags} />
            <label>
              <input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} />{" "}
              {t("counterparties.show_archived")}
            </label>
          </div>
          {list.isLoading ? <Loading /> : null}
          {list.data?.counterparties.length === 0 ? <p className="muted">{t(`${api}.none`)}</p> : null}
          <table>
            <tbody>
              {(list.data?.counterparties ?? []).map((card) => (
                <tr key={card.id}>
                  <td>
                    <Link to={companyPath(company, cardPath(of, card.id))}>{card.name}</Link>{" "}
                    <RowTags tags={tagsOf(card.id)} />
                  </td>
                  <td>{t(`clients.kind_${card.kind}`)}</td>
                  <td className="small muted">{card.contacts.map((c) => c.value).join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      ) : (
        <p className="muted">{t(`${api}.list_not_allowed`)}</p>
      )}
    </>
  );
}

/** A new counterparty: person — first and last name and a contact; company — name and a contact
 * (§ 6.2.7–6.2.8). Also used right at a receipt (§ 11.2.8). */
export function CounterpartyForm({
  onCreated,
  name = "",
}: {
  onCreated?: (card: Counterparty) => void;
  /** The name of the company to start with: a supplier read from its document. */
  name?: string;
}) {
  const { t } = useTranslation();
  const company = useCompany();
  const client = useQueryClient();
  const [draft, setDraft] = useState<Draft>({ ...EMPTY, company_name: name });
  const create = useCommand<Record<string, unknown>, { counterparty: Counterparty }>(
    `/c/${company.public_id}/counterparties`,
  );

  async function submit(event: FormEvent) {
    event.preventDefault();
    event.stopPropagation();
    const contacts = contactsOf(draft.contacts);
    const body: Record<string, unknown> =
      draft.kind === "person"
        ? {
            kind: "person",
            first_name: draft.first_name,
            last_name: draft.last_name,
            middle_name: draft.middle_name,
            birth_date: draft.birth_date || null,
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

const CARD_TABS = ["main", "finance", "history", "settings"] as const;
// The settlements with an investor are not decided yet (OPEN, 26.11.2026): his card has no
// tabs of the money and of the supplies of a counterparty. The vehicles he gave into the
// management are a tab of their own (customer's decision of 26.11.2026).
const INVESTOR_TABS = ["main", "vehicles", "settings"] as const;
type CardTab = (typeof CARD_TABS)[number] | (typeof INVESTOR_TABS)[number];

/** Card of an investor (customer's decision of 26.11.2026): the data, the vehicles he gave into
 * the management, the link with a profile. */
export function InvestorPage() {
  return <CounterpartyPage list="investor" />;
}

/** The window that ends the management needs the whole card of the vehicle: its version and
 * its documents. */
function EndFromInvestor({ id, onClose, onDone }: { id: string; onClose: () => void; onDone: () => void }) {
  const company = useCompany();
  const found = useApi<{ vehicle: Vehicle }>(["vehicle", company.public_id, id], `/c/${company.public_id}/vehicles/${id}`);
  if (!found.data) return null;
  return <EndManagementModal vehicle={found.data.vehicle} onClose={onClose} onDone={onDone} />;
}

/** The vehicles an investor gave into the management. The management of one of them is ended
 * right here (customer's decision of 27.11.2026). */
function InvestorVehicles({ id }: { id: string }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const found = useApi<{ vehicles: { id: string; name: string }[]; count: number }>(
    ["investor-vehicles", company.public_id, id],
    `/c/${company.public_id}/investors/${id}/vehicles`,
  );
  const vehicles = found.data?.vehicles ?? [];
  const hidden = (found.data?.count ?? 0) - vehicles.length;
  const [ending, setEnding] = useState<{ id: string; name: string } | null>(null);
  const [ended, setEnded] = useState("");
  return (
    <Card>
      <div data-testid="investor-vehicles">
        {ended ? (
          <p className="alert ok small" data-testid="investor-vehicle-ended">
            {t("investors.vehicle_ended", { name: ended })}
          </p>
        ) : null}
        {found.isLoading ? <Loading /> : null}
        {found.data && found.data.count === 0 ? <p className="muted">{t("investors.vehicles_none")}</p> : null}
        {vehicles.map((v) => (
          <div key={v.id} className="row-actions" style={{ justifyContent: "flex-start" }}>
            <Link to={companyPath(company, `/vehicles/${v.id}`)}>{v.name}</Link>
            {can("vehicle.relation") ? (
              <button type="button" className="secondary small-button" onClick={() => setEnding(v)}>
                {t("vehicles.end_management_button")}
              </button>
            ) : null}
          </div>
        ))}
        {hidden > 0 ? <p className="muted small">{t("investors.vehicles_hidden", { count: hidden })}</p> : null}
        {ending ? (
          <EndFromInvestor
            id={ending.id}
            onClose={() => setEnding(null)}
            onDone={() => {
              setEnded(ending.name);
              setEnding(null);
              void client.invalidateQueries({ queryKey: ["investor-vehicles", company.public_id, id] });
            }}
          />
        ) : null}
      </div>
    </Card>
  );
}

/** Card of a counterparty (§ 6.6.4–6.6.5): the same as the card of a client (customer's
 * decision of 15.11.2026) — text with a window of editing, and the same tabs. */
export function CounterpartyPage({ list: of = "counterparty" }: { list?: PartyList }) {
  const { t } = useTranslation();
  const { id = "" } = useParams();
  const company = useCompany();
  const can = useCan();
  const navigate = useNavigate();
  const route = useLocation();
  const queryClient = useQueryClient();
  const investor = of === "investor";
  const base = `/c/${company.public_id}/${apiList(of)}/${id}`;
  const card = useApi<{ counterparty: Counterparty }>([of, company.public_id, id], base);
  const archive = useCommand<{ expected_version: number }, unknown>(`${base}/archive`);
  const restore = useCommand<{ expected_version: number }, unknown>(`${base}/restore`);
  const [params, setParams] = useSearchParams();
  const section = `${of}_card`;
  const firstTab = useFirstTab(section);
  if (card.isLoading) return <div className="page full"><Loading /></div>;
  const data = card.data?.counterparty;
  if (!data) return <div className="page full"><ErrorMessage error={null} /></div>;
  const current = data;
  const tabs: readonly CardTab[] = investor ? INVESTOR_TABS : CARD_TABS;
  const tab = tabs.find((x) => x === params.get("tab")) ?? firstTab(tabs) ?? "main";
  const money = can("finance.account.view") || can("finance.settlement.manage") || can("finance.payout");
  const mergedInto = data.merged_into;

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: [of, company.public_id, id] });
    await queryClient.invalidateQueries({ queryKey: [apiList(of), company.public_id] });
    await queryClient.invalidateQueries({ queryKey: ["counterparty-categories", company.public_id] });
  }

  return (
    <div className="page full">
      {/* The button «Back» stands before the tabs, outside their list. */}
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <IconButton
          icon="back"
          label={t("common.back")}
          onClick={() =>
            route.key === "default" ? navigate(companyPath(company, listPath(of))) : navigate(-1)
          }
        />
        <SectionTabs
          section={section}
          current={tab}
          tabs={tabs.map((key) => ({ key, text: t(`cards.tab_${key}`) }))}
          onSelect={(key) => setParams({ tab: key }, { replace: true })}
          style={{ flex: 1 }}
        />
      </div>
      <TagsLine kind={investor ? "investor" : "counterparty"} id={data.id} />
      <PersonRoles cardType={of} id={data.id} />
      {mergedInto ? (
        <div className="alert warn">
          {t("cards.merged_banner")}{" "}
          <Link to={companyPath(company, cardPath(of, mergedInto))}>{t("cards.open_main")}</Link>
        </div>
      ) : null}
      {data.provisional ? <div className="alert warn">{t("cards.provisional_banner")}</div> : null}
      {tab === "main" ? (
        <>
          <MainCard cardType={of} card={data} badges={<CardBadges card={data} cardType={of} />} onSaved={refresh} />
          <DocumentsCard cardType={of} card={data} onSaved={refresh} />
        </>
      ) : null}
      {tab === "vehicles" ? <InvestorVehicles id={data.id} /> : null}
      {tab === "finance" ? (
        money ? (
          <SupplierCard counterpartyId={data.id} name={t("cards.money_supplier")} />
        ) : (
          <Card>
            <p className="muted">{t("cards.money_hidden")}</p>
          </Card>
        )
      ) : null}
      {tab === "history" ? (
        <>
          {money ? <SupplierHistoryCard counterpartyId={data.id} /> : null}
          {data.kind === "company" ? <ContractorHistoryCard counterpartyId={data.id} /> : null}
          {!money && data.kind !== "company" ? (
            <Card>
              <p className="muted">{t("cards.history_none")}</p>
            </Card>
          ) : null}
        </>
      ) : null}
      {tab === "settings" ? (
        <CardSettings
          section={`${of}_settings`}
          access={
            <div style={{ maxWidth: 760 }}>
              <ProfileLinkCard cardType={of} cardId={data.id} cardKind={data.kind} version={data.version} onChanged={refresh} />
            </div>
          }
        >
          {/* § 18.1.1–18.1.2: documents of the relations with the counterparty. */}
          {investor ? null : <LinkedDocuments objectType="counterparty" objectId={data.id} />}
          <MergeCard cardType={of} card={data} onChanged={refresh} />
          {data.external_id ? (
            <Card title={t("cards.other_title")}>
              <Facts rows={[[t("cards.field_external_id"), data.external_id]]} />
              <p className="muted small">{t("cards.external_id_hint")}</p>
            </Card>
          ) : null}
          {(data.state === "active" && can(`${of}.archive`)) || (data.state === "archived" && can(`${of}.restore`)) ? (
            <Card>
              <ErrorMessage error={archive.error ?? restore.error} />
              <div className="actions">
                {data.state === "active" ? (
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
                ) : (
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
                )}
              </div>
            </Card>
          ) : null}
        </CardSettings>
      ) : null}
    </div>
  );
}
