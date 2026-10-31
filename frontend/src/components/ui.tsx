import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import type { ApiError } from "../api/client";

/** Shows a refusal by its translation key; the server text is never shown (A08). */
export function ErrorMessage({ error }: { error: ApiError | null }) {
  const { t } = useTranslation();
  if (!error) return null;
  const key = `errors.${error.code.toLowerCase()}`;
  const text = t(key, {
    field: error.field ?? "",
    ...error.params,
    defaultValue: t("errors.unknown", { code: error.code }),
  });
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

/** § 4.3.2: archiving the last tariff, standalone service or service work leaves a location not
 * configured — allowed after this warning; existing operations go on. */
export function UnconfiguredConfirm({
  error,
  pending,
  onConfirm,
}: {
  error: ApiError | null;
  pending: boolean;
  onConfirm: () => void;
}) {
  const { t } = useTranslation();
  if (error?.code !== "CONTEXT_WOULD_BE_UNCONFIGURED") return null;
  return (
    <div className="alert warn">
      <p>{t("common.unconfigured_warning", { locations: (error.params.locations as string[] | undefined)?.join(", ") ?? "" })}</p>
      <button type="button" className="danger" disabled={pending} onClick={onConfirm}>
        {t("common.archive_anyway")}
      </button>
    </div>
  );
}
