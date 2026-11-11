import { useEffect, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";

import type { ApiError } from "../../api/client";
import { useApi } from "../../api/queries";
import { Card, ErrorMessage, Loading } from "../../components/ui";
import { companyPath, useCompany } from "../../layout/CompanyShell";

export type SearchHit = {
  kind: string;
  id: string;
  label: string;
  detail: string;
  exact: boolean;
  extra?: Record<string, string>;
};
type SearchAnswer = { query: string; groups: { kind: string; hits: SearchHit[] }[] };

/** Where a found record is opened (the cards of the sections). */
export function hitPath(hit: SearchHit): string {
  switch (hit.kind) {
    case "order":
      return `/orders/${hit.id}`;
    case "rental":
      return `/rentals/${hit.id}`;
    case "service_operation":
      return `/service/operations/${hit.id}`;
    case "trip":
      return `/trips/${hit.id}`;
    case "payment":
      return `/finance?q=${encodeURIComponent(hit.label)}`;
    case "document":
      return `/documents/${hit.id}`;
    case "vehicle":
      return `/vehicles/${hit.id}`;
    case "client":
      return `/clients/${hit.id}`;
    case "counterparty":
      return `/clients/counterparties/${hit.id}`;
    case "item":
      return `/warehouse/items/${hit.id}`;
    case "task":
      return `/tasks/${hit.id}`;
    default:
      return "";
  }
}

/** The single search line in the top bar of the company (ТЗ § 2.8.2). */
export function SearchBox() {
  const { t } = useTranslation();
  const { cmp = "" } = useParams();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [text, setText] = useState("");
  const current = params.get("q") ?? "";
  useEffect(() => {
    if (current) setText(current);
  }, [current]);

  function submit(event: FormEvent) {
    event.preventDefault();
    const value = text.trim();
    if (value) navigate(`/c/${cmp}/search?q=${encodeURIComponent(value)}`);
  }

  return (
    <form className="topbar-search" role="search" onSubmit={submit}>
      <input
        type="search"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={t("search.placeholder")}
        aria-label={t("search.placeholder")}
        maxLength={100}
      />
    </form>
  );
}

/** The results of the single search, grouped by the kind of record; exact matches first in each
 * group (§ 2.8.3). Each group is found with the rights of its section. */
export function SearchPage() {
  const { t } = useTranslation();
  const company = useCompany();
  const [params] = useSearchParams();
  const q = (params.get("q") ?? "").trim();
  const found = useApi<SearchAnswer>(
    ["search", company.public_id, q],
    `/c/${company.public_id}/search?q=${encodeURIComponent(q)}`,
    q.length > 0,
  );
  const groups = found.data?.groups ?? [];
  return (
    <div className="page">
      <h1>{t("search.title")}</h1>
      {q ? <p className="muted">{t("search.for", { q })}</p> : <p className="muted">{t("search.hint")}</p>}
      {found.isLoading ? <Loading /> : null}
      <ErrorMessage error={found.error as ApiError | null} />
      {q && found.data && groups.length === 0 ? <p>{t("search.nothing")}</p> : null}
      {groups.map((group) => (
        <Card key={group.kind} title={t(`search.kind_${group.kind}`)}>
          <ul className="plain-list">
            {group.hits.map((hit) => (
              <li key={hit.id}>
                <Link to={companyPath(company, hitPath(hit))}>{hitTitle(hit, t)}</Link>
                {hit.detail ? <span className="muted"> · {hitDetail(hit, t)}</span> : null}
                {hit.exact ? <span className="badge ok" style={{ marginLeft: 8 }}>{t("search.exact")}</span> : null}
              </li>
            ))}
          </ul>
        </Card>
      ))}
      <p className="muted small">{t("search.scope_note")}</p>
    </div>
  );
}

type T = ReturnType<typeof useTranslation>["t"];

function hitTitle(hit: SearchHit, t: T): string {
  const numbered = ["order", "rental", "service_operation", "trip", "payment", "document", "task"];
  if (numbered.includes(hit.kind)) return t(`search.numbered_${hit.kind}`, { number: hit.label || "—" });
  return hit.label;
}

function hitDetail(hit: SearchHit, t: T): string {
  if (hit.kind === "order") return t(`orders.state_${hit.detail}`, { defaultValue: hit.detail });
  // A system task is titled by its event (§ 19.1.9).
  if (hit.kind === "task" && /^[a-z_]+(\.[a-z_]+)+$/.test(hit.detail)) {
    return t(`tasks.system_${hit.detail.replaceAll(".", "_")}`, { defaultValue: hit.detail });
  }
  if (hit.kind === "client" || hit.kind === "counterparty") return t(`search.party_${hit.detail}`, { defaultValue: "" });
  return hit.detail;
}

/** The search inside a section (§ 2.8.3): the text goes to the section's own list. */
export function SectionSearch({
  value,
  onSearch,
  compact = false,
}: {
  value: string;
  onSearch: (text: string) => void;
  /** A short field that widens when used; Enter searches (the garage, decision of 11.11.2026). */
  compact?: boolean;
}) {
  const { t } = useTranslation();
  const [text, setText] = useState(value);
  return (
    <form
      className={compact ? "section-search compact" : "actions section-search"}
      role="search"
      onSubmit={(event: FormEvent) => {
        event.preventDefault();
        onSearch(text.trim());
      }}
    >
      <input
        type="search"
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          if (!e.target.value) onSearch("");
        }}
        placeholder={t("search.section_placeholder")}
        aria-label={t("search.section_placeholder")}
        maxLength={100}
      />
      {compact ? null : (
        <button type="submit" className="secondary">
          {t("search.title")}
        </button>
      )}
    </form>
  );
}
