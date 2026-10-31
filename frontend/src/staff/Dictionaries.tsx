import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { ApiError } from "../api/client";
import { useApi } from "../api/queries";
import type { PartAttribute } from "../api/types";
import { useCommand } from "../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../components/ui";

type Manufacturer = { id: string; name: string; aliases: string[] };
type OtherGroup = { text: string; variants: string[]; items: number; companies: number };
type Names = { ru: string; en: string; th: string };
const EMPTY: Names = { ru: "", en: "", th: "" };

function splitList(text: string): string[] {
  return [...new Set(text.split(",").map((s) => s.trim()).filter(Boolean))];
}

function NamesFields({ value, onChange, label }: { value: Names; onChange: (value: Names) => void; label: string }) {
  return (
    <div className="row">
      {(["ru", "en", "th"] as const).map((lang) => (
        <Field key={lang} label={`${label} · ${lang.toUpperCase()}`}>
          <input value={value[lang]} onChange={(e) => onChange({ ...value, [lang]: e.target.value })} />
        </Field>
      ))}
    </div>
  );
}

/** § 11.1.12–11.1.14: the platform administrator keeps the system dictionaries of the warehouse
 * catalog — manufacturers with their spellings, the linking of «Other» values entered by
 * companies, and the system attributes of parts with their values. */
export function DictionariesPage() {
  const { t } = useTranslation();
  return (
    <div className="page">
      <h1>{t("staff.nav_dictionaries")}</h1>
      <Manufacturers />
      <Attributes />
    </div>
  );
}

function Manufacturers() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const list = useApi<{ manufacturers: Manufacturer[] }>(["staff", "manufacturers"], "/staff/warehouse/manufacturers");
  const other = useApi<{ other: OtherGroup[] }>(["staff", "manufacturer-other"], "/staff/warehouse/manufacturer-other");
  const [name, setName] = useState("");
  const [aliases, setAliases] = useState("");
  const [reason, setReason] = useState("");
  const add = useCommand<Record<string, unknown>, unknown>("/staff/warehouse/manufacturers");
  const link = useCommand<Record<string, unknown>, { linked: number }>("/staff/warehouse/manufacturer-link");
  const [linking, setLinking] = useState<Record<string, string>>({});
  const [linkReason, setLinkReason] = useState("");
  const [linked, setLinked] = useState<number | null>(null);
  const refresh = async () => {
    await client.invalidateQueries({ queryKey: ["staff", "manufacturers"] });
    await client.invalidateQueries({ queryKey: ["staff", "manufacturer-other"] });
  };
  const manufacturers = list.data?.manufacturers ?? [];
  return (
    <>
      <Card title={t("dictionaries.other_title")}>
        <p className="muted small">{t("dictionaries.other_hint")}</p>
        {other.isLoading ? <Loading /> : null}
        <ErrorMessage error={other.error as ApiError | null} />
        {other.data && !other.data.other.length ? <p className="muted">{t("staff.none")}</p> : null}
        {other.data?.other.length ? (
          <>
            <table>
              <thead>
                <tr>
                  <th>{t("dictionaries.text")}</th>
                  <th>{t("dictionaries.items")}</th>
                  <th>{t("dictionaries.manufacturer")}</th>
                </tr>
              </thead>
              <tbody>
                {other.data.other.map((g) => (
                  <tr key={g.text}>
                    <td>
                      {g.text}
                      {g.variants.length > 1 ? <div className="small muted">{g.variants.join(" · ")}</div> : null}
                    </td>
                    <td className="small">{t("dictionaries.items_in", { items: g.items, companies: g.companies })}</td>
                    <td>
                      <select value={linking[g.text] ?? ""} onChange={(e) => setLinking({ ...linking, [g.text]: e.target.value })}>
                        <option value="">—</option>
                        {manufacturers.map((m) => (
                          <option key={m.id} value={m.id}>
                            {m.name}
                          </option>
                        ))}
                      </select>{" "}
                      <button
                        type="button"
                        className="small"
                        disabled={!linking[g.text] || !linkReason.trim() || link.pending}
                        onClick={async () => {
                          const result = await link.run({ texts: g.variants, manufacturer_id: linking[g.text], reason: linkReason });
                          if (result) {
                            setLinked(result.linked);
                            await refresh();
                          }
                        }}
                      >
                        {t("dictionaries.link")}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Field label={t("staff.reason")} hint={t("dictionaries.link_hint")}>
              <input value={linkReason} onChange={(e) => setLinkReason(e.target.value)} />
            </Field>
          </>
        ) : null}
        {linked !== null ? <p className="alert ok">{t("dictionaries.linked", { count: linked })}</p> : null}
        <ErrorMessage error={link.error} />
      </Card>
      <Card title={t("dictionaries.manufacturers")}>
        <ul>
          {manufacturers.map((m) => (
            <li key={m.id}>
              {m.name}
              {m.aliases.length ? <span className="muted small"> · {m.aliases.join(", ")}</span> : null}
            </li>
          ))}
        </ul>
        {list.data && !manufacturers.length ? <p className="muted">{t("staff.none")}</p> : null}
        <h3>{t("dictionaries.add_manufacturer")}</h3>
        <div className="row">
          <Field label={t("dictionaries.name")}>
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label={t("dictionaries.aliases")} hint={t("dictionaries.aliases_hint")}>
            <input value={aliases} onChange={(e) => setAliases(e.target.value)} />
          </Field>
          <Field label={t("staff.reason")}>
            <input value={reason} onChange={(e) => setReason(e.target.value)} />
          </Field>
        </div>
        <ErrorMessage error={add.error} />
        <div className="actions">
          <button
            type="button"
            disabled={!name.trim() || !reason.trim() || add.pending}
            onClick={async () => {
              if ((await add.run({ name, aliases: splitList(aliases), reason })) !== null) {
                setName("");
                setAliases("");
                setReason("");
                await refresh();
              }
            }}
          >
            {t("dictionaries.add")}
          </button>
        </div>
      </Card>
    </>
  );
}

function Attributes() {
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const list = useApi<{ attributes: PartAttribute[] }>(["staff", "part-attributes"], "/staff/warehouse/attributes");
  const [code, setCode] = useState("");
  const [names, setNames] = useState<Names>(EMPTY);
  const [multiple, setMultiple] = useState(false);
  const [values, setValues] = useState<{ code: string; name: Names }[]>([{ code: "", name: EMPTY }]);
  const [reason, setReason] = useState("");
  const add = useCommand<Record<string, unknown>, unknown>("/staff/warehouse/attributes");
  const change = useCommand<Record<string, unknown>, unknown>((b) => `/staff/warehouse/attributes/${String(b.id)}`, "PATCH");
  const lang = (["ru", "en", "th"].includes(i18n.language.slice(0, 2)) ? i18n.language.slice(0, 2) : "en") as keyof Names;
  const refresh = () => client.invalidateQueries({ queryKey: ["staff", "part-attributes"] });
  return (
    <Card title={t("dictionaries.attributes")}>
      <p className="muted small">{t("dictionaries.attributes_hint")}</p>
      <ul>
        {(list.data?.attributes ?? []).map((a) => (
          <li key={a.id} className={a.state === "archived" ? "muted" : ""}>
            <strong>{a.name[lang]}</strong> <span className="small muted">({a.code})</span>
            {a.multiple ? <span className="badge" style={{ marginLeft: 6 }}>{t("dictionaries.multiple")}</span> : null} ·{" "}
            {a.values.map((v) => v.name[lang]).join(", ")}{" "}
            <button
              type="button"
              className="link"
              onClick={async () => {
                const why = window.prompt(t("staff.reason"));
                if (why && (await change.run({ id: a.id, archived: a.state === "active", reason: why })) !== null) await refresh();
              }}
            >
              {a.state === "active" ? t("dictionaries.archive") : t("dictionaries.restore")}
            </button>
          </li>
        ))}
      </ul>
      <ErrorMessage error={change.error} />
      <h3>{t("dictionaries.add_attribute")}</h3>
      <Field label={t("dictionaries.code")} hint={t("dictionaries.code_hint")}>
        <input value={code} onChange={(e) => setCode(e.target.value)} />
      </Field>
      <NamesFields value={names} onChange={setNames} label={t("dictionaries.name")} />
      <label>
        <input type="checkbox" checked={multiple} onChange={(e) => setMultiple(e.target.checked)} /> {t("dictionaries.multiple_hint")}
      </label>
      {values.map((v, index) => (
        <div key={index} className="line-card">
          <Field label={t("dictionaries.value_code")}>
            <input
              value={v.code}
              onChange={(e) => setValues(values.map((x, i) => (i === index ? { ...x, code: e.target.value } : x)))}
            />
          </Field>
          <NamesFields
            value={v.name}
            onChange={(name) => setValues(values.map((x, i) => (i === index ? { ...x, name } : x)))}
            label={t("dictionaries.value")}
          />
        </div>
      ))}
      <button type="button" className="secondary small" onClick={() => setValues([...values, { code: "", name: EMPTY }])}>
        {t("dictionaries.add_value")}
      </button>
      <Field label={t("staff.reason")}>
        <input value={reason} onChange={(e) => setReason(e.target.value)} />
      </Field>
      <ErrorMessage error={add.error} />
      <div className="actions">
        <button
          type="button"
          disabled={!code.trim() || !reason.trim() || add.pending}
          onClick={async () => {
            const body = { code: code.trim(), name: names, multiple, values: values.filter((v) => v.code.trim()), reason };
            if ((await add.run(body)) !== null) {
              setCode("");
              setNames(EMPTY);
              setValues([{ code: "", name: EMPTY }]);
              setReason("");
              await refresh();
            }
          }}
        >
          {t("dictionaries.add")}
        </button>
      </div>
    </Card>
  );
}
