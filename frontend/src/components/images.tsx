import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError, fileUrl, uploadFile } from "../api/client";
import { ErrorMessage } from "./ui";

/** Photo or logo; without one — initials on a circle. */
export function Avatar({
  file,
  name,
  size = 32,
  square = false,
}: {
  file: string | null;
  name: string;
  size?: number;
  square?: boolean;
}) {
  const initials = name
    .split(/[\s@.]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");
  const style = { width: size, height: size, fontSize: Math.round(size * 0.4) };
  const className = `avatar${square ? " square" : ""}`;
  return file ? (
    <img className={className} style={style} src={fileUrl(file)} alt="" />
  ) : (
    <span className={className} style={style} aria-hidden="true">
      {initials || "?"}
    </span>
  );
}

/** Choose an image: it is uploaded at once, the caller links it by id when saving. */
export function ImagePicker({
  purpose,
  value,
  name,
  onChange,
  disabled,
  square,
}: {
  purpose: "avatar" | "company_logo";
  value: string | null;
  name: string;
  onChange: (file: string | null) => void;
  disabled?: boolean;
  square?: boolean;
}) {
  const { t } = useTranslation();
  const input = useRef<HTMLInputElement>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  async function choose(file: File | undefined) {
    if (!file) return;
    setPending(true);
    setError(null);
    try {
      onChange((await uploadFile(purpose, file)).id);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught : new ApiError(0, null));
    } finally {
      setPending(false);
      if (input.current) input.current.value = "";
    }
  }

  return (
    <div className="image-picker">
      <Avatar file={value} name={name} size={64} square={square} />
      {!disabled ? (
        <div className="stack">
          <input
            ref={input}
            type="file"
            accept="image/png,image/jpeg,image/webp"
            hidden
            onChange={(e) => void choose(e.target.files?.[0])}
          />
          <div className="actions" style={{ marginTop: 0 }}>
            <button
              type="button"
              className="secondary"
              disabled={pending}
              onClick={() => input.current?.click()}
            >
              {value ? t("images.replace") : t("images.choose")}
            </button>
            {value ? (
              <button type="button" className="secondary" onClick={() => onChange(null)}>
                {t("images.remove")}
              </button>
            ) : null}
          </div>
          <small className="muted">{t("images.hint")}</small>
          <ErrorMessage error={error} />
        </div>
      ) : null}
    </div>
  );
}
