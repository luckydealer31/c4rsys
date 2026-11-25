import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router";
import { ProfileLinkCard } from "./ProfileLinkCard";

import { useApi } from "../../api/queries";
import type { Counterparty } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, IconButton, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { SectionTabs } from "../../layout/SectionTabs";
import { useFirstTab } from "../../layout/tabOrder";
import { TagsLine } from "../tags/Tags";
import { CardBadges } from "./ClientsPage";
import { ContactsEditor, contactsOf, type ContactRow } from "./ContactsEditor";
import { ContractorHistoryCard, MergeCard } from "./CardExtras";
import { DocumentsCard, Facts, MainCard, SupplierHistoryCard } from "./CardParts";
import { LinkedDocuments } from "../documents/DocumentsPage";
import { SupplierCard } from "../finance/Settlements";

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
export function CounterpartiesList() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const [query, setQuery] = useState("");
  const [archived, setArchived] = useState(false);
  // The list is filtered by the categories of the directory of the company (15.11.2026).
  const [category, setCategory] = useState("");
  const categories = useApi<{ categories: string[] }>(
    ["counterparty-categories", company.public_id],
    `/c/${company.public_id}/counterparties/categories`,
    can("counterparty.view"),
  );
  const list = useApi<{ counterparties: Counterparty[] }>(
    ["counterparties", company.public_id, query, archived, category],
    `/c/${company.public_id}/counterparties?q=${encodeURIComponent(query)}&state=${archived ? "archived" : "active"}&category=${encodeURIComponent(category)}`,
    can("counterparty.view"),
  );
  return (
    <>
      {can("counterparty.view") ? (
        <Card>
          <div className="list-bar">
            <input
              type="search"
              className="list-search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("counterparties.search")}
              aria-label={t("counterparties.search")}
            />
            {categories.data?.categories.length ? (
              <select value={category} aria-label={t("cards.field_category")} onChange={(e) => setCategory(e.target.value)}>
                <option value="">{t("cards.category_all")}</option>
                {categories.data.categories.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            ) : null}
            <label>
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
                  <td>{card.category}</td>
                  <td className="small muted">{card.contacts.map((c) => c.value).join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      ) : (
        <p className="muted">{t("counterparties.list_not_allowed")}</p>
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

const CARD_TABS = ["main", "finance", "history", "more"] as const;

/** Card of a counterparty (§ 6.6.4–6.6.5): the same as the card of a client (customer's
 * decision of 15.11.2026) — text with a window of editing, and the same tabs. */
export function CounterpartyPage() {
  const { t } = useTranslation();
  const { id = "" } = useParams();
  const company = useCompany();
  const can = useCan();
  const navigate = useNavigate();
  const route = useLocation();
  const queryClient = useQueryClient();
  const base = `/c/${company.public_id}/counterparties/${id}`;
  const card = useApi<{ counterparty: Counterparty }>(["counterparty", company.public_id, id], base);
  const archive = useCommand<{ expected_version: number }, unknown>(`${base}/archive`);
  const restore = useCommand<{ expected_version: number }, unknown>(`${base}/restore`);
  const [params, setParams] = useSearchParams();
  const firstTab = useFirstTab("counterparty_card");
  if (card.isLoading) return <div className="page full"><Loading /></div>;
  const data = card.data?.counterparty;
  if (!data) return <div className="page full"><ErrorMessage error={null} /></div>;
  const current = data;
  const tab = CARD_TABS.find((x) => x === params.get("tab")) ?? firstTab(CARD_TABS) ?? "main";
  const money = can("finance.account.view") || can("finance.settlement.manage") || can("finance.payout");
  const mergedInto = data.merged_into;

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: ["counterparty", company.public_id, id] });
    await queryClient.invalidateQueries({ queryKey: ["counterparties", company.public_id] });
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
            route.key === "default" ? navigate(companyPath(company, "/clients?tab=counterparties")) : navigate(-1)
          }
        />
        <SectionTabs
          section="counterparty_card"
          current={tab}
          tabs={CARD_TABS.map((key) => ({ key, text: t(`cards.tab_${key}`) }))}
          onSelect={(key) => setParams({ tab: key }, { replace: true })}
          style={{ flex: 1 }}
        />
      </div>
      <TagsLine kind="counterparty" id={data.id} />
      {mergedInto ? (
        <div className="alert warn">
          {t("cards.merged_banner")}{" "}
          <Link to={companyPath(company, `/clients/counterparties/${mergedInto}`)}>{t("cards.open_main")}</Link>
        </div>
      ) : null}
      {data.provisional ? <div className="alert warn">{t("cards.provisional_banner")}</div> : null}
      {tab === "main" ? (
        <>
          <MainCard cardType="counterparty" card={data} badges={<CardBadges card={data} cardType="counterparty" />} onSaved={refresh} />
          <DocumentsCard cardType="counterparty" card={data} onSaved={refresh} />
        </>
      ) : null}
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
      {tab === "more" ? (
        <>
          {/* The link with the profile is made and broken here (decision of 15.11.2026). */}
          <div style={{ maxWidth: 760 }}>
            <ProfileLinkCard cardType="counterparty" cardId={data.id} cardKind={data.kind} version={data.version} onChanged={refresh} />
          </div>
          {/* § 18.1.1–18.1.2: documents of the relations with the counterparty. */}
          <LinkedDocuments objectType="counterparty" objectId={data.id} />
          <MergeCard cardType="counterparty" card={data} onChanged={refresh} />
          {data.external_id ? (
            <Card title={t("cards.other_title")}>
              <Facts rows={[[t("cards.field_external_id"), data.external_id]]} />
              <p className="muted small">{t("cards.external_id_hint")}</p>
            </Card>
          ) : null}
          {(data.state === "active" && can("counterparty.archive")) || (data.state === "archived" && can("counterparty.restore")) ? (
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
        </>
      ) : null}
    </div>
  );
}
