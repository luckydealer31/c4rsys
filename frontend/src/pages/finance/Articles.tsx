import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "../../api/queries";
import type { FinanceArticle } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { useCan, useCompany } from "../../layout/CompanyShell";

export function useArticles() {
  const company = useCompany();
  return useApi<{ articles: FinanceArticle[] }>(
    ["finance-articles", company.public_id],
    `/c/${company.public_id}/finance-articles`,
  );
}

/** A starting article is shown by its translated name until the company renames it; an own
 * name is shown as entered (§ 17.2.8). */
export function useArticleName(): (article: FinanceArticle) => string {
  const { t } = useTranslation();
  return (article) => article.name ?? t(`finance_articles.code_${article.code}`);
}

/** Income and expense articles of the company (§ 17.2.8). */
export function Articles() {
  const { t } = useTranslation();
  const can = useCan();
  const articles = useArticles();
  const editable = can("finance.article.manage");
  return (
    <>
      {(["income", "expense"] as const).map((kind) => (
        <Card key={kind} title={t(`finance_articles.title_${kind}`)}>
          {articles.isLoading ? <Loading /> : null}
          <table>
            <thead>
              <tr>
                <th>{t("finance_articles.name")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {(articles.data?.articles ?? [])
                .filter((a) => a.kind === kind)
                .map((article) => (
                  <ArticleRow key={article.id} article={article} editable={editable} />
                ))}
            </tbody>
          </table>
        </Card>
      ))}
      {editable ? <CreateArticle /> : null}
    </>
  );
}

function useRefresh() {
  const client = useQueryClient();
  const company = useCompany();
  return () => client.invalidateQueries({ queryKey: ["finance-articles", company.public_id] });
}

function ArticleRow({ article, editable }: { article: FinanceArticle; editable: boolean }) {
  const { t } = useTranslation();
  const company = useCompany();
  const refresh = useRefresh();
  const nameOf = useArticleName();
  const [name, setName] = useState(nameOf(article));
  const path = `/c/${company.public_id}/finance-articles/${article.id}`;
  const rename = useCommand<Record<string, unknown>, unknown>(path, "PATCH");
  const archive = useCommand<Record<string, unknown>, unknown>(`${path}/archive`);
  const restore = useCommand<Record<string, unknown>, unknown>(`${path}/restore`);
  const remove = useCommand<Record<string, unknown>, unknown>(path, "DELETE");
  const version = { expected_version: article.version };

  async function run(command: { run: (b: Record<string, unknown>) => Promise<unknown> }, body = {}) {
    if ((await command.run({ ...version, ...body })) !== null) await refresh();
  }

  const hint = article.code && !article.name ? t(`finance_articles.hint_${article.code}`, "") : "";
  return (
    <tr>
      <td>
        {editable && article.state === "active" ? (
          <input value={name} onChange={(e) => setName(e.target.value)} />
        ) : (
          nameOf(article)
        )}
        {hint ? <div className="muted">{hint}</div> : null}
        <ErrorMessage error={rename.error ?? archive.error ?? restore.error ?? remove.error} />
      </td>
      <td>
        {article.state === "archived" ? <span className="badge">{t("finance_articles.archived")}</span> : null}
        {editable ? (
          <div className="actions" style={{ marginTop: 0 }}>
            {article.state === "active" ? (
              <>
                <button
                  type="button"
                  className="secondary"
                  disabled={name === nameOf(article) || !name.trim()}
                  onClick={() => run(rename, { name })}
                >
                  {t("common.save")}
                </button>
                <button type="button" className="secondary" onClick={() => run(archive)}>
                  {t("finance_articles.archive")}
                </button>
              </>
            ) : (
              <button type="button" className="secondary" onClick={() => run(restore)}>
                {t("finance_articles.restore")}
              </button>
            )}
            {!article.code && !article.used ? (
              <button type="button" className="secondary" onClick={() => run(remove)}>
                {t("finance_articles.delete")}
              </button>
            ) : null}
          </div>
        ) : null}
      </td>
    </tr>
  );
}

function CreateArticle() {
  const { t } = useTranslation();
  const company = useCompany();
  const refresh = useRefresh();
  const [form, setForm] = useState({ kind: "expense", name: "" });
  const create = useCommand<Record<string, string>, unknown>(
    `/c/${company.public_id}/finance-articles`,
  );

  async function submit(event: FormEvent) {
    event.preventDefault();
    if ((await create.run(form)) !== null) {
      setForm({ ...form, name: "" });
      await refresh();
    }
  }

  return (
    <Card title={t("finance_articles.create_title")}>
      <form className="stack" onSubmit={submit}>
        <div className="row">
          <Field label={t("finance_articles.kind")}>
            <select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
              <option value="income">{t("finance_articles.kind_income")}</option>
              <option value="expense">{t("finance_articles.kind_expense")}</option>
            </select>
          </Field>
          <Field label={t("finance_articles.name")}>
            <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </Field>
        </div>
        <ErrorMessage error={create.error} />
        <div className="actions">
          <button type="submit" disabled={!form.name.trim() || create.pending}>
            {t("finance_articles.create")}
          </button>
        </div>
      </form>
    </Card>
  );
}
