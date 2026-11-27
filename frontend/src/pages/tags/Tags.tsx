import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { api, ApiError } from "../../api/client";
import { useApi } from "../../api/queries";
import { Icon } from "../../components/icons";
import { ErrorMessage, Info, Loading, Modal } from "../../components/ui";
import { companyPath, useCompany } from "../../layout/CompanyShell";

/** Tags (customer's decisions of 17.11.2026): marks of the company put on records of any
 * section, to find them together; and personal tags, which their employee alone sees. A tag of
 * the company is made with the right «Manage tags»; the others choose from the list. */
export type Tag = {
  id: string;
  name: string;
  color: string;
  /** Seen by its owner alone; drawn in another shape, so the two are not confused. */
  personal: boolean;
  archived: boolean;
  version: number;
  count?: number;
  /** The group of a tag of the company, if it is in one. */
  group_id?: string | null;
};
/** A group of tags (customer's decisions of 17.11.2026): everybody sees it; its tags are made
 * and changed by the employees named in it and by whoever manages the tags; `kinds` — the kinds
 * of records its tags are offered for, empty — for every kind. */
export type TagGroup = {
  id: string;
  name: string;
  /** A group the system keeps: «storage_places» — the storage places of the warehouse. Its name
   * is the interface's, and it is not removed. */
  code?: string;
  kinds: string[];
  members: { id: string; name: string }[];
  can_create: boolean;
  version: number;
};
export type TagList = {
  tags: Tag[];
  groups: TagGroup[];
  can_manage: boolean;
  palette: string[];
  kinds: string[];
  /** Whom a group may name; given to whoever manages the tags. */
  employees?: { id: string; name: string }[];
};

/** Whether a tag is offered for a kind of records: one of no group, or of a group for it. */
export function offeredFor(tag: Tag, groups: TagGroup[], kind: string) {
  const group = groups.find((g) => g.id === tag.group_id);
  return !group || group.kinds.length === 0 || group.kinds.includes(kind);
}
/** The kinds of records a tag is put on — as the single search names them. */
export type TagKind = "client" | "counterparty" | "investor" | "vehicle" | "item" | "order" | "rental" | "service_operation";

export function useTags() {
  const company = useCompany();
  return useApi<TagList>(["tags", company.public_id], `/c/${company.public_id}/tags`);
}

/** For the search inside a section (customer's decision of 17.11.2026): tells whether a record
 * of the kind is marked with a tag whose name has the text in it — a tag of the company or the
 * user's own. The lists that are searched on the page use it; the others ask the server. */
export function useTagMatch(kind: TagKind) {
  const company = useCompany();
  const tags = useTags();
  const links = useApi<{ links: Record<string, string[]> }>(
    ["tag-links", company.public_id, kind],
    `/c/${company.public_id}/tag-links?type=${kind}`,
  );
  const names = new Map((tags.data?.tags ?? []).filter((tag) => !tag.archived).map((tag) => [tag.id, tag.name.toLowerCase()]));
  return (id: string, text: string) => {
    const needle = text.trim().toLowerCase();
    return Boolean(needle) && (links.data?.links[id] ?? []).some((tag) => names.get(tag)?.includes(needle));
  };
}

/** The tags of the records of a kind, for a list (customer's decision of 18.11.2026: a tag must
 * be seen in the list, not only in the card). Returns the tags of a record by its id — of the
 * company and the user's own. */
export function useTagsOf(kind: TagKind) {
  const company = useCompany();
  const tags = useTags();
  const links = useApi<{ links: Record<string, string[]> }>(
    ["tag-links", company.public_id, kind],
    `/c/${company.public_id}/tag-links?type=${kind}`,
  );
  const byId = new Map((tags.data?.tags ?? []).filter((tag) => !tag.archived).map((tag) => [tag.id, tag]));
  return (id: string): Tag[] =>
    (links.data?.links[id] ?? [])
      .flatMap((tag) => byId.get(tag) ?? [])
      .sort((a, b) => Number(a.personal) - Number(b.personal) || a.name.localeCompare(b.name));
}

/** The filter of a list by tags (customer's decision of 28.11.2026): the tags offered for the
 * kind are chosen one by one; the list shows the records that carry every chosen tag. */
export function TagFilter({ kind, chosen, onChange }: { kind: TagKind; chosen: string[]; onChange: (ids: string[]) => void }) {
  const { t } = useTranslation();
  const tags = useTags();
  const offered = (tags.data?.tags ?? []).filter((tag) => !tag.archived && offeredFor(tag, tags.data?.groups ?? [], kind));
  if (!offered.length) return null;
  const picked = offered.filter((tag) => chosen.includes(tag.id));
  const rest = offered.filter((tag) => !chosen.includes(tag.id));
  return (
    <span className="tag-filter" data-testid="tag-filter">
      {picked.map((tag) => (
        <button
          key={tag.id}
          type="button"
          className="tag-filter-chosen"
          aria-label={`${t("tags.filter_remove")}: ${tag.name}`}
          title={t("tags.filter_remove")}
          onClick={() => onChange(chosen.filter((id) => id !== tag.id))}
        >
          <TagChip tag={tag} /> ×
        </button>
      ))}
      {rest.length ? (
        <select
          value=""
          aria-label={t("tags.filter")}
          onChange={(e) => {
            if (e.target.value) onChange([...chosen, e.target.value]);
          }}
        >
          <option value="">{t(picked.length ? "tags.filter_more" : "tags.filter")}</option>
          {rest.map((tag) => (
            <option key={tag.id} value={tag.id}>
              {tag.name}
            </option>
          ))}
        </select>
      ) : null}
    </span>
  );
}

/** The tags of a row of a list: small marks that lead to the page of the tag. */
export function RowTags({ tags }: { tags: Tag[] }) {
  if (!tags.length) return null;
  return (
    <span className="row-tags">
      {tags.map((tag) => (
        <TagChip key={tag.id} tag={tag} link />
      ))}
    </span>
  );
}

/** A tag as it is shown everywhere: a filled pill for a tag of the company, an outlined square
 * mark with a little person for a personal one. */
export function TagChip({ tag, link = false }: { tag: Tag; link?: boolean }) {
  const { t } = useTranslation();
  const company = useCompany();
  const className = `tag-chip tag-${tag.color}${tag.personal ? " personal" : ""}`;
  const inside = (
    <>
      {tag.personal ? <Icon name="user" size={12} /> : null}
      {tag.name}
    </>
  );
  const title = tag.personal ? t("tags.personal_mark") : undefined;
  return link ? (
    <Link className={className} title={title} to={companyPath(company, `/company/tags/${tag.id}`)}>
      {inside}
    </Link>
  ) : (
    <span className={className} title={title}>
      {inside}
    </span>
  );
}

/** The name and the colour of a tag: a new one, or one being changed. */
export function TagForm({
  tag,
  personal,
  palette,
  groups = [],
  groupId = null,
  canMove = false,
  onClose,
}: {
  tag?: Tag;
  personal: boolean;
  palette: string[];
  /** The groups a tag of the company may stand in. */
  groups?: TagGroup[];
  /** The group of a new tag. */
  groupId?: string | null;
  /** The group is chosen and changed by whoever manages the tags. */
  canMove?: boolean;
  onClose: (saved?: Tag) => void;
}) {
  const { t } = useTranslation();
  const company = useCompany();
  const client = useQueryClient();
  const [name, setName] = useState(tag?.name ?? "");
  const [color, setColor] = useState(tag?.color ?? palette[0] ?? "gray");
  const [group, setGroup] = useState(tag ? (tag.group_id ?? "") : (groupId ?? ""));
  const grouped = !personal && !tag?.personal;
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);
    try {
      const base = `/c/${company.public_id}/tags`;
      const saved = tag
        ? await api<{ tag: Tag }>(`${base}/${tag.id}`, {
            method: "PATCH",
            body: { name, color, expected_version: tag.version, ...(grouped && canMove ? { group_id: group || null } : {}) },
            idempotencyKey: crypto.randomUUID(),
          })
        : await api<{ tag: Tag }>(base, {
            method: "POST",
            body: { name, color, personal, ...(grouped ? { group_id: group || null } : {}) },
            idempotencyKey: crypto.randomUUID(),
          });
      await client.invalidateQueries({ queryKey: ["tags", company.public_id] });
      await client.invalidateQueries({ queryKey: ["tag-links", company.public_id] });
      await client.invalidateQueries({ queryKey: ["tag", company.public_id] });
      onClose(saved.tag);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught : new ApiError(0, null));
    } finally {
      setPending(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit}>
      <label className="field">
        <span>{t("tags.name")}</span>
        <input value={name} maxLength={40} autoFocus onChange={(e) => setName(e.target.value)} />
      </label>
      {grouped && canMove && groups.length ? (
        <label className="field">
          <span>{t("tags.group")}</span>
          <select value={group} onChange={(e) => setGroup(e.target.value)}>
            <option value="">{t("tags.no_group")}</option>
            {groups.map((g) => (
              <option key={g.id} value={g.id}>
                {g.code ? t(`tags.system_group_${g.code}`) : g.name}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      <div className="field">
        <span>{t("tags.color")}</span>
        <div className="tag-palette" role="radiogroup" aria-label={t("tags.color")}>
          {palette.map((key) => (
            <button
              key={key}
              type="button"
              role="radio"
              aria-checked={color === key}
              aria-label={t(`tags.color_${key}`)}
              title={t(`tags.color_${key}`)}
              className={`tag-swatch tag-${key}`}
              onClick={() => setColor(key)}
            />
          ))}
        </div>
      </div>
      {name.trim() ? (
        <div>
          <TagChip tag={{ id: "", name: name.trim(), color, personal: tag?.personal ?? personal, archived: false, version: 0 }} />
        </div>
      ) : null}
      <ErrorMessage error={error} />
      <div className="actions end">
        <button type="submit" disabled={pending || !name.trim()}>
          {t("common.save")}
        </button>
      </div>
    </form>
  );
}

/** The tags of a record in its card: the marks, and the window where they are chosen. Nothing
 * is changed until the window is opened by the button. */
export function TagsLine({ kind, id }: { kind: TagKind; id: string }) {
  const { t } = useTranslation();
  const company = useCompany();
  const [choosing, setChoosing] = useState(false);
  const path = `/c/${company.public_id}/tag-links?type=${kind}&id=${id}`;
  const found = useApi<{ tags: Tag[] }>(["tag-links", company.public_id, kind, id], path);
  const tags = found.data?.tags ?? [];
  // A record the employee does not see in its section has no tags to show.
  if (found.error) return null;
  return (
    <div className="tags-line">
      {tags.map((tag) => (
        <TagChip key={tag.id} tag={tag} link />
      ))}
      {/* Easy to see (the customer did not find a pale one, 18.11.2026): in the colour of the
          actions, and said in full while the record has no tag yet. */}
      <button type="button" className="tag-add bright" aria-label={t("tags.change")} title={t("tags.change")} onClick={() => setChoosing(true)}>
        <Icon name="plus" size={12} /> {t(tags.length ? "tags.add" : "tags.add_first")}
      </button>
      {choosing ? (
        <Modal title={t("tags.title")} onClose={() => setChoosing(false)}>
          <TagPicker kind={kind} id={id} chosen={tags} onClose={() => setChoosing(false)} />
        </Modal>
      ) : null}
    </div>
  );
}

function TagPicker({ kind, id, chosen, onClose }: { kind: TagKind; id: string; chosen: Tag[]; onClose: () => void }) {
  const { t } = useTranslation();
  const company = useCompany();
  const client = useQueryClient();
  const list = useTags();
  const [picked, setPicked] = useState<Set<string>>(() => new Set(chosen.map((tag) => tag.id)));
  const [adding, setAdding] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  if (list.isLoading || !list.data) return <Loading />;
  const active = list.data.tags.filter((tag) => !tag.archived);
  const toggle = (tag: Tag) => {
    const next = new Set(picked);
    if (next.has(tag.id)) next.delete(tag.id);
    else next.add(tag.id);
    setPicked(next);
  };
  const group = (tags: Tag[]) =>
    tags.map((tag) => (
      <button key={tag.id} type="button" className="tag-choice" aria-pressed={picked.has(tag.id)} onClick={() => toggle(tag)}>
        <TagChip tag={tag} />
      </button>
    ));

  async function save() {
    setPending(true);
    setError(null);
    try {
      const answer = await api<{ tags: Tag[] }>(`/c/${company.public_id}/tag-links`, {
        method: "PUT",
        // The archived ones are not shown, and are not asked for.
        body: { type: kind, id, tag_ids: active.filter((tag) => picked.has(tag.id)).map((tag) => tag.id) },
        idempotencyKey: crypto.randomUUID(),
      });
      client.setQueryData(["tag-links", company.public_id, kind, id], answer);
      await client.invalidateQueries({ queryKey: ["tags", company.public_id] });
      await client.invalidateQueries({ queryKey: ["tag", company.public_id] });
      await client.invalidateQueries({ queryKey: ["tag-links", company.public_id, kind], exact: true });
      onClose();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught : new ApiError(0, null));
    } finally {
      setPending(false);
    }
  }

  // The tags of the company offered here: of no group, or of a group for this kind of records;
  // one already put is shown whatever its group.
  const groups = list.data.groups;
  const ofCompany = active.filter((tag) => !tag.personal && (offeredFor(tag, groups, kind) || picked.has(tag.id)));
  const loose = ofCompany.filter((tag) => !groups.some((g) => g.id === tag.group_id));
  const mine = active.filter((tag) => tag.personal);
  return (
    <div className="stack">
      {loose.length || !ofCompany.length ? (
        <div>
          <h3>{t("tags.company_title")}</h3>
          {loose.length ? <div className="tag-choices">{group(loose)}</div> : <p className="muted">{t("tags.none")}</p>}
        </div>
      ) : null}
      {groups.map((g) => {
        const tags = ofCompany.filter((tag) => tag.group_id === g.id);
        return tags.length ? (
          <div key={g.id}>
            <h3>{g.name}</h3>
            <div className="tag-choices">{group(tags)}</div>
          </div>
        ) : null;
      })}
      <div>
        <h3>
          {t("tags.personal_title")}
          <Info text={t("tags.personal_hint")} />
        </h3>
        <div className="tag-choices">
          {group(mine)}
          {adding ? null : (
            <button type="button" className="tag-add" onClick={() => setAdding(true)}>
              <Icon name="plus" size={12} /> {t("tags.new_personal")}
            </button>
          )}
        </div>
        {adding ? (
          <div className="tag-new">
            <TagForm
              personal
              palette={list.data.palette}
              onClose={(saved) => {
                setAdding(false);
                // A tag just made is put on the record at once.
                if (saved) setPicked(new Set([...picked, saved.id]));
              }}
            />
          </div>
        ) : null}
      </div>
      <ErrorMessage error={error} />
      <div className="actions end">
        <button type="button" disabled={pending} onClick={() => void save()}>
          {t("common.save")}
        </button>
      </div>
    </div>
  );
}
