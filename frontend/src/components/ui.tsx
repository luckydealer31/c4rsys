import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import type { ApiError } from "../api/client";

/** Shows a refusal by its translation key; the server text is never shown (A08). */
export function ErrorMessage({ error }: { error: ApiError | null }) {
  const { t } = useTranslation();
  if (!error) return null;
  const key = `errors.${error.code.toLowerCase()}`;
  const text = t(key, { ...error.params, defaultValue: t("errors.unknown", { code: error.code }) });
  return (
    <div className="alert error" role="alert">
      {text}
    </div>
  );
}

export function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint ? <small className="muted">{hint}</small> : null}
    </label>
  );
}

export function Card({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <section className="card">
      {title ? <h2>{title}</h2> : null}
      {children}
    </section>
  );
}

export function Loading() {
  const { t } = useTranslation();
  return <p className="muted">{t("common.loading")}</p>;
}
