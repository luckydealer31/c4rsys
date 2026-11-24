import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate } from "react-router";

import { IconButton } from "../components/ui";

/** The head of a page opened from another one: the button «Back» and the title in one line, as
 * in the card of a vehicle. «Back» returns to where the page was opened from — a card, a list,
 * a schedule; opened by a direct address, it leads to `fallback` (customer's remarks of
 * 24.11.2026). */
export function BackTitle({ fallback, children }: { fallback: string; children: ReactNode }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const route = useLocation();
  return (
    <div className="card-head">
      <IconButton
        icon="back"
        label={t("common.back")}
        onClick={() => (route.key !== "default" ? navigate(-1) : navigate(fallback))}
      />
      {children}
    </div>
  );
}
