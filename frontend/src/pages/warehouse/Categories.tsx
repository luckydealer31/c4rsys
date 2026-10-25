import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../../api/client";
import { useApi } from "../../api/queries";
import { Field } from "../../components/ui";

type Names = { ru: string; en: string; th: string };
export type PartCategory = {
  id: string;
  code: string;
  names: Names;
  subcategories: { id: string; code: string; names: Names }[];
};
type Suggestion = { category: string; subcategory: string; matched: number };

/** The platform catalog of categories and subcategories of parts (decision К1). */
export function usePartCategories() {
  return useApi<{ categories: PartCategory[] }>(["part-categories"], "/catalog/part-categories");
}

/** A name in the language of the interface, Russian when there is none. */
export function useCategoryName() {
  const { i18n } = useTranslation();
  const lang = i18n.language.slice(0, 2) as keyof Names;
  return (names: Names) => names[lang] || names.ru;
}

export function useCategoryLabel() {
  const tree = usePartCategories();
  const name = useCategoryName();
  return (categoryId: string | null, subcategoryId: string | null) => {
    const category = tree.data?.categories.find((c) => c.id === categoryId);
    if (!category) return "";
    const sub = category.subcategories.find((s) => s.id === subcategoryId);
    return sub ? `${name(category.names)} → ${name(sub.names)}` : name(category.names);
  };
}

/** Category and subcategory of an item (decisions К2, К3): while the name is typed the best match
 * is set at once, other matches are offered; the employee may change it or leave the item
 * without a category. A manual choice is not overwritten by later typing. */
export function PartCategoryField({
  name,
  category,
  subcategory,
  editable,
  autoFromStart,
  onChange,
}: {
  name: string;
  category: string;
  subcategory: string;
  editable: boolean;
  autoFromStart: boolean;
  onChange: (category: string, subcategory: string) => void;
}) {
  const { t } = useTranslation();
  const tree = usePartCategories();
  const label = useCategoryName();
  const [manual, setManual] = useState(!autoFromStart);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const firstName = useRef(name);

  useEffect(() => {
    if (!editable || name === firstName.current) return;
    const timer = window.setTimeout(async () => {
      const found = name.trim()
        ? (await api<{ suggestions: Suggestion[] }>(`/catalog/part-categories/suggest?name=${encodeURIComponent(name)}`)).suggestions
        : [];
      setSuggestions(found);
      const best = found[0];
      if (!manual) onChange(best?.category ?? "", best?.subcategory ?? "");
    }, 350);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reacts to the name only
  }, [name]);

  const categories = tree.data?.categories ?? [];
  const current = categories.find((c) => c.id === category);
  const describe = (s: Suggestion) => {
    const c = categories.find((x) => x.id === s.category);
    const sub = c?.subcategories.find((x) => x.id === s.subcategory);
    return c && sub ? `${label(c.names)} → ${label(sub.names)}` : "";
  };
  const others = suggestions.filter((s) => s.subcategory !== subcategory);

  return (
    <div className="stack">
      <div className="row">
        <Field label={t("categories.category")}>
          <select
            value={category}
            disabled={!editable}
            onChange={(e) => {
              setManual(true);
              onChange(e.target.value, "");
            }}
          >
            <option value="">{t("categories.none")}</option>
            {categories.map((c) => (
              <option key={c.id} value={c.id}>
                {label(c.names)}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("categories.subcategory")}>
          <select
            value={subcategory}
            disabled={!editable || !current}
            onChange={(e) => {
              setManual(true);
              onChange(category, e.target.value);
            }}
          >
            <option value="">—</option>
            {(current?.subcategories ?? []).map((s) => (
              <option key={s.id} value={s.id}>
                {label(s.names)}
              </option>
            ))}
          </select>
        </Field>
      </div>
      {editable && others.length ? (
        <p className="small">
          {t("categories.alternatives")}{" "}
          {others.map((s) => (
            <button
              key={s.subcategory}
              type="button"
              className="link"
              style={{ marginRight: 10 }}
              onClick={() => {
                setManual(true);
                onChange(s.category, s.subcategory);
              }}
            >
              {describe(s)}
            </button>
          ))}
        </p>
      ) : null}
      {editable ? <p className="muted small">{t("categories.hint")}</p> : null}
    </div>
  );
}
