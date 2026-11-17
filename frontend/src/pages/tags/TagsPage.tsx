import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useLocation, useNavigate, useParams } from "react-router";

import { api, ApiError } from "../../api/client";
import { useApi } from "../../api/queries";
import { Icon } from "../../components/icons";
import { Card, ErrorMessage, IconButton, Info, Loading, Modal, Switch } from "../../components/ui";
import { CompanyTabs } from "../../layout/CompanyTabs";
import { companyPath, useCompany } from "../../layout/CompanyShell";
import { hitDetail, hitPath, hitTitle, type SearchHit } from "../search/SearchPage";
import { TagChip, TagForm, useTags, type Tag, type TagGroup, type TagList } from "./Tags";

type Editing = { tag?: Tag; personal: boolean; groupId?: string | null };

/** The tab «Tags» of the company (customer's decisions of 17.11.2026): the tags of the company
 * by their groups — seen by everybody; made and changed with the right «Manage tags» or, in a
 * group, by the employees named in it — and the user's own personal tags. A tag leads to
 * everything it is put on. */
export function TagsPage() {
  const { t } = useTranslation();
  const company = useCompany();
  const client = useQueryClient();
  const list = useTags();
  const [editing, setEditing] = useState<Editing | null>(null);
  // The group being changed, or «new» for a new one.
  const [grouping, setGrouping] = useState<TagGroup | "new" | null>(null);
  const [archived, setArchived] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const data = list.data;
  const shown = (data?.tags ?? []).filter((tag) => archived || !tag.archived);
  const hasArchived = (data?.tags ?? []).some((tag) => tag.archived);

  async function setState(tag: Tag, action: "archive" | "restore") {
    setError(null);
    try {
      await api(`/c/${company.public_id}/tags/${tag.id}/${action}`, {
        method: "POST",
        body: { expected_version: tag.version },
        idempotencyKey: crypto.randomUUID(),
      });
      await client.invalidateQueries({ queryKey: ["tags", company.public_id] });
      await client.invalidateQueries({ queryKey: ["tag-links", company.public_id] });
    } catch (caught) {
      setError(caught instanceof ApiError ? caught : new ApiError(0, null));
    }
  }

  const rows = (tags: Tag[], may: boolean) => (
    <ul className="tag-rows">
      {tags.map((tag) => (
        <li key={tag.id} className={tag.archived ? "archived" : undefined}>
          <TagChip tag={tag} link />
          <span className="muted small">{t("tags.count", { count: tag.count ?? 0 })}</span>
          {tag.archived ? <span className="badge">{t("tags.archived")}</span> : null}
          {may ? (
            <span className="tag-row-actions">
              {tag.archived ? (
                <IconButton icon="restore" label={t("tags.restore")} onClick={() => void setState(tag, "restore")} />
              ) : (
                <>
                  <IconButton
                    icon="edit"
                    label={t("tags.edit")}
                    onClick={() => setEditing({ tag, personal: tag.personal, groupId: tag.group_id })}
                  />
                  <IconButton icon="archive" label={t("tags.archive")} onClick={() => void setState(tag, "archive")} />
                </>
              )}
            </span>
          ) : null}
        </li>
      ))}
    </ul>
  );

  const groups = data?.groups ?? [];
  const ofCompany = shown.filter((tag) => !tag.personal);
  const loose = ofCompany.filter((tag) => !groups.some((g) => g.id === tag.group_id));
  const mine = shown.filter((tag) => tag.personal);
  return (
    <div className="page full">
      <h1 className="sr-only">{t("tags.title")}</h1>
      <CompanyTabs current="tags" />
      <div className="split">
        <div>
          {list.isLoading ? <Loading /> : null}
          <ErrorMessage error={(list.error as ApiError | null) ?? error} />
          {hasArchived ? (
            <div className="list-bar">
              <Switch name={t("tags.show_archived")} checked={archived} onChange={setArchived}>
                {t("tags.show_archived")}
              </Switch>
            </div>
          ) : null}
          {data ? (
            <>
              {/* The tags of no group: «Tags of the company», or «Without a group» beside groups. */}
              {loose.length || !groups.length ? (
                <Card title={t(groups.length ? "tags.no_group" : "tags.company_title")}>
                  {loose.length ? rows(loose, data.can_manage) : <p className="muted">{t("tags.none")}</p>}
                </Card>
              ) : null}
              {groups.map((group) => {
                const tags = ofCompany.filter((tag) => tag.group_id === group.id);
                return (
                  <section key={group.id} className="card tag-group">
                    <div className="tag-group-head">
                      <h2>{group.name}</h2>
                      {group.can_create ? (
                        <button
                          type="button"
                          className="tag-add"
                          aria-label={t("tags.add_to_group", { group: group.name })}
                          title={t("tags.add_to_group", { group: group.name })}
                          onClick={() => setEditing({ personal: false, groupId: group.id })}
                        >
                          <Icon name="plus" size={12} /> {t("tags.add")}
                        </button>
                      ) : null}
                      {data.can_manage ? (
                        <IconButton
                          icon="edit"
                          label={t("tags.group_edit_named", { group: group.name })}
                          onClick={() => setGrouping(group)}
                        />
                      ) : null}
                    </div>
                    <p className="muted small tag-group-note">
                      {t("tags.group_where", {
                        kinds: group.kinds.length
                          ? group.kinds.map((kind) => t(`search.kind_${kind}`)).join(", ")
                          : t("tags.group_everywhere"),
                      })}
                      {group.members.length
                        ? ` · ${t("tags.group_makers", { names: group.members.map((m) => m.name).join(", ") })}`
                        : ""}
                    </p>
                    {tags.length ? rows(tags, group.can_create) : <p className="muted">{t("tags.none")}</p>}
                  </section>
                );
              })}
              <section className="card">
                <h2>
                  {t("tags.personal_title")}
                  <Info text={t("tags.personal_hint")} />
                </h2>
                {mine.length ? rows(mine, true) : <p className="muted">{t("tags.none_personal")}</p>}
              </section>
            </>
          ) : null}
        </div>
        <aside className="side-actions" aria-label={t("vehicles.actions_title")}>
          {data?.can_manage ? (
            <>
              <button type="button" className="button secondary" onClick={() => setEditing({ personal: false })}>
                <Icon name="plus" size={16} /> {t("tags.new")}
              </button>
              <button type="button" className="button secondary" onClick={() => setGrouping("new")}>
                <Icon name="plus" size={16} /> {t("tags.group_new")}
              </button>
            </>
          ) : null}
          <button type="button" className="button secondary" onClick={() => setEditing({ personal: true })}>
            <Icon name="plus" size={16} /> {t("tags.new_personal")}
          </button>
        </aside>
      </div>
      {editing && data ? (
        <Modal
          title={t(editing.tag ? "tags.edit" : editing.personal ? "tags.new_personal" : "tags.new")}
          onClose={() => setEditing(null)}
        >
          <TagForm
            tag={editing.tag}
            personal={editing.personal}
            palette={data.palette}
            groups={groups}
            groupId={editing.groupId}
            canMove={data.can_manage}
            onClose={() => setEditing(null)}
          />
        </Modal>
      ) : null}
      {grouping && data ? (
        <Modal title={t(grouping === "new" ? "tags.group_new" : "tags.group_edit")} onClose={() => setGrouping(null)}>
          <GroupForm group={grouping === "new" ? undefined : grouping} list={data} onClose={() => setGrouping(null)} />
        </Modal>
      ) : null}
    </div>
  );
}

/** A group of tags: its name, where its tags are offered and who makes them. */
function GroupForm({ group, list, onClose }: { group?: TagGroup; list: TagList; onClose: () => void }) {
  const { t } = useTranslation();
  const company = useCompany();
  const client = useQueryClient();
  const [name, setName] = useState(group?.name ?? "");
  const [kinds, setKinds] = useState<Set<string>>(() => new Set(group?.kinds ?? []));
  const [members, setMembers] = useState<Set<string>>(() => new Set((group?.members ?? []).map((m) => m.id)));
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const base = `/c/${company.public_id}/tag-groups`;
  const flip = (set: Set<string>, key: string) => {
    const next = new Set(set);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    return next;
  };

  async function send(method: "POST" | "PATCH" | "DELETE") {
    setPending(true);
    setError(null);
    try {
      const body = { name, kinds: [...kinds], member_ids: [...members] };
      await api(method === "POST" ? base : `${base}/${group?.id}`, {
        method,
        body: method === "DELETE" ? undefined : method === "PATCH" ? { ...body, expected_version: group?.version } : body,
        idempotencyKey: crypto.randomUUID(),
      });
      await client.invalidateQueries({ queryKey: ["tags", company.public_id] });
      onClose();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught : new ApiError(0, null));
    } finally {
      setPending(false);
    }
  }

  return (
    <form
      className="stack"
      onSubmit={(event: FormEvent) => {
        event.preventDefault();
        void send(group ? "PATCH" : "POST");
      }}
    >
      <label className="field">
        <span>{t("tags.group_name")}</span>
        <input value={name} maxLength={40} autoFocus onChange={(e) => setName(e.target.value)} />
      </label>
      <fieldset className="tag-checks">
        <legend>
          {t("tags.group_kinds")}
          <Info text={t("tags.group_kinds_hint")} />
        </legend>
        {list.kinds.map((kind) => (
          <label key={kind}>
            <input type="checkbox" checked={kinds.has(kind)} onChange={() => setKinds(flip(kinds, kind))} />{" "}
            {t(`search.kind_${kind}`)}
          </label>
        ))}
      </fieldset>
      <fieldset className="tag-checks">
        <legend>
          {t("tags.group_members")}
          <Info text={t("tags.group_members_hint")} />
        </legend>
        {(list.employees ?? []).map((employee) => (
          <label key={employee.id}>
            <input type="checkbox" checked={members.has(employee.id)} onChange={() => setMembers(flip(members, employee.id))} />{" "}
            {employee.name}
          </label>
        ))}
      </fieldset>
      <ErrorMessage error={error} />
      <div className="actions end">
        {group ? (
          <button type="button" className="secondary" disabled={pending} title={t("tags.group_remove_hint")} onClick={() => void send("DELETE")}>
            {t("tags.group_remove")}
          </button>
        ) : null}
        <button type="submit" disabled={pending || !name.trim()}>
          {t("common.save")}
        </button>
      </div>
    </form>
  );
}

type TagObjects = { tag: Tag; can_manage: boolean; objects: Record<string, SearchHit[]> };
// The order of the groups on the page of a tag.
const KINDS = ["client", "counterparty", "vehicle", "rental", "order", "service_operation", "item", "task"];

/** The page of a tag: everything it is put on, by sections — only what the employee may see
 * there. */
export function TagPage() {
  const { t } = useTranslation();
  const { id = "" } = useParams();
  const company = useCompany();
  const navigate = useNavigate();
  const route = useLocation();
  const found = useApi<TagObjects>(["tag", company.public_id, id], `/c/${company.public_id}/tags/${id}/objects`);
  const list = useTags();
  const [editing, setEditing] = useState(false);
  const data = found.data;
  const kinds = KINDS.filter((kind) => data?.objects[kind]?.length);
  const group = (list.data?.groups ?? []).find((g) => g.id === data?.tag.group_id);
  return (
    <div className="page full">
      <CompanyTabs current="tags" />
      <div className="card-head">
        <IconButton
          icon="back"
          label={t("common.back")}
          onClick={() => (route.key === "default" ? navigate(companyPath(company, "/company/tags")) : navigate(-1))}
        />
        {data ? (
          <h1 className="tag-title">
            <TagChip tag={data.tag} />
          </h1>
        ) : null}
        {group ? <span className="muted">{group.name}</span> : null}
        {data?.tag.archived ? <span className="badge">{t("tags.archived")}</span> : null}
        {data?.can_manage && !data.tag.archived ? (
          <IconButton icon="edit" label={t("tags.edit")} onClick={() => setEditing(true)} />
        ) : null}
      </div>
      {found.isLoading ? <Loading /> : null}
      <ErrorMessage error={found.error as ApiError | null} />
      {data && kinds.length === 0 ? (
        <Card>
          <p className="muted">{t("tags.nothing_marked")}</p>
        </Card>
      ) : null}
      {kinds.map((kind) => (
        <Card key={kind} title={t(`search.kind_${kind}`)}>
          <ul className="plain-list">
            {(data?.objects[kind] ?? []).map((hit) => (
              <li key={hit.id}>
                <Link to={companyPath(company, hitPath(hit))}>{hitTitle(hit, t)}</Link>
                {hit.detail ? <span className="muted"> · {hitDetail(hit, t)}</span> : null}
              </li>
            ))}
          </ul>
        </Card>
      ))}
      {data && kinds.length ? <p className="muted small">{t("tags.scope_note")}</p> : null}
      {editing && data && list.data ? (
        <Modal title={t("tags.edit")} onClose={() => setEditing(false)}>
          <TagForm
            tag={data.tag}
            personal={data.tag.personal}
            palette={list.data.palette}
            groups={list.data.groups}
            canMove={list.data.can_manage}
            onClose={() => setEditing(false)}
          />
        </Modal>
      ) : null}
    </div>
  );
}
