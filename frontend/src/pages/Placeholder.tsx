import { useTranslation } from "react-i18next";

/** A section whose screens come with a later part of T18. */
export function Placeholder({ section }: { section: string }) {
  const { t } = useTranslation();
  return (
    <div className="page">
      <h1>{t(`nav.${section}`)}</h1>
      <p className="muted">{t("common.coming_later")}</p>
    </div>
  );
}
