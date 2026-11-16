import { useEffect, useRef, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import type { ApiError } from "../api/client";
import { Icon, type IconName } from "./icons";

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
  hintTitle,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
  /** Shown when the pointer is over the hint: the full name behind a short record. */
  hintTitle?: string;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint ? (
        <small className="muted" title={hintTitle}>
          {hint}
        </small>
      ) : null}
    </label>
  );
}

/** A small «i» beside a field or a title (decision of 16.11.2026): an explanation is shown
 * when the pointer is over it or it has the focus, instead of a note always printed on the
 * screen. */
export function Info({ text }: { text: string }) {
  return (
    <span className="info-tip" tabIndex={0} role="note" aria-label={text} data-tip={text}>
      i
    </span>
  );
}

/** A switch: on to the right and green, off to the left (customer's decision of 16.11.2026).
 * `name` is what it switches, said to a screen reader; the words beside it say the state. */
export function Switch({
  name,
  checked,
  onChange,
  children,
}: {
  name: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  children: ReactNode;
}) {
  return (
    <label className="switch">
      <span className="switch-box">
        <input
          type="checkbox"
          role="switch"
          aria-label={name}
          checked={checked}
          onChange={(e) => onChange(e.target.checked)}
        />
        <span className="switch-track" aria-hidden="true" />
      </span>
      <span>{children}</span>
    </label>
  );
}

/** A field with its caption inside the box, above the value (customer's decision of
 * 14.11.2026): takes no line of its own. */
export function FloatField({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="float-field">
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

/** A small action of a row shown by an icon; the label is its tooltip and its accessible name
 * (customer's decision of 11.11.2026: rows carry small icons, not large buttons). */
export function IconButton({
  icon,
  label,
  onClick,
  disabled = false,
  primary = false,
}: {
  icon: IconName;
  label: string;
  onClick: () => void;
  disabled?: boolean;
  primary?: boolean;
}) {
  return (
    <button
      type="button"
      className={primary ? "icon-button primary" : "icon-button"}
      title={label}
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
    >
      <Icon name={icon} size={18} />
    </button>
  );
}

/** A window over the page for a short form; closed by its cross, Esc or a click outside. */
export function Modal({
  title,
  onClose,
  children,
  wide = false,
  roomy = false,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  /** For a picture or a page of a document: as wide as the screen allows. */
  wide?: boolean;
  /** For a form with three fields in a line. */
  roomy?: boolean;
}) {
  const { t } = useTranslation();
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal();
    return () => dialog?.close();
  }, []);
  return (
    <dialog
      ref={ref}
      className={wide ? "modal modal-wide" : roomy ? "modal modal-roomy" : "modal"}
      aria-label={title}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target === ref.current) onClose();
      }}
    >
      <div className="modal-body">
        <div className="page-head">
          <h2 style={{ margin: 0 }}>{title}</h2>
          <IconButton icon="close" label={t("common.cancel")} onClick={onClose} />
        </div>
        {children}
      </div>
    </dialog>
  );
}
