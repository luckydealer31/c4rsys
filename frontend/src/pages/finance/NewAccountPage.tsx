import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router";

import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field } from "../../components/ui";
import { companyPath, useCompany } from "../../layout/CompanyShell";
import { currencies } from "../../time";

const STEPS = ["type", "currency", "name"] as const;

/** Step-by-step creation of a cash desk or a bank account (customer's decision of 11.11.2026),
 * as the creation of a vehicle: the type, the currency, the name. */
export function NewAccountPage() {
  const { t } = useTranslation();
  const company = useCompany();
  const navigate = useNavigate();
  const client = useQueryClient();
  const [form, setForm] = useState({ type: "cash", currency: company.main_currency ?? "", name: "" });
  const [index, setIndex] = useState(0);
  const create = useCommand<Record<string, string>, unknown>(`/c/${company.public_id}/cash-accounts`);
  const step = STEPS[index] ?? "type";
  const last = step === "name";
  const ready = step === "currency" ? form.currency !== "" : step === "name" ? form.name.trim() !== "" : true;
  const back = companyPath(company, "/finance?tab=accounts");

  async function next(event: FormEvent) {
    event.preventDefault();
    if (!ready || create.pending) return;
    if (!last) return setIndex(index + 1);
    if ((await create.run(form)) !== null) {
      await client.invalidateQueries({ queryKey: ["cash-accounts", company.public_id] });
      // The setup wizard shows readiness from the same data (§ 22.1.12).
      await client.invalidateQueries({ queryKey: ["readiness", company.public_id] });
      navigate(back);
    }
  }

  let content: ReactNode;
  if (step === "type") {
    content = (
      <div className="stack">
        {(["cash", "bank"] as const).map((type) => (
          <label key={type} style={{ display: "block" }}>
            <input type="radio" name="type" checked={form.type === type} onChange={() => setForm({ ...form, type })} />{" "}
            {t(`cash.type_${type}`)}
          </label>
        ))}
      </div>
    );
  } else if (step === "currency") {
    content = (
      <Field label={t("cash.currency")} hint={t("cash.currency_hint")}>
        <select autoFocus value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value })}>
          <option value="">—</option>
          {currencies().map((code) => (
            <option key={code} value={code}>
              {code}
            </option>
          ))}
        </select>
      </Field>
    );
  } else {
    content = (
      <>
        <Field label={t("cash.name")}>
          <input autoFocus value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        </Field>
        <ErrorMessage error={create.error} />
      </>
    );
  }

  return (
    <div className="page">
      <p>
        <Link to={back}>← {t("cash.title")}</Link>
      </p>
      <h1>{t("cash.create_title")}</h1>
      <p className="muted">{t("wizard.step", { n: index + 1, total: STEPS.length })}</p>
      <form onSubmit={next} key={step}>
        <Card title={t(`cash.${step}`)}>
          {content}
          <div className="actions">
            {index > 0 ? (
              <button type="button" className="secondary" onClick={() => setIndex(index - 1)}>
                {t("wizard.back")}
              </button>
            ) : null}
            <button type="submit" disabled={!ready || create.pending}>
              {last ? t("cash.create") : t("wizard.next")}
            </button>
          </div>
        </Card>
      </form>
    </div>
  );
}
