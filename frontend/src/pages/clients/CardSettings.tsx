import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router";

import { SectionTabs } from "../../layout/SectionTabs";
import { useFirstTab } from "../../layout/tabOrder";

const PARTS = ["access", "other"] as const;

/** The tab «Settings» of a card of a client, a counterparty or an investor (customer's decision
 * of 26.11.2026), split like the settings of a vehicle: «Access» — the link with a c4rsys
 * profile; «Other» — the rest of the former tab «More». */
export function CardSettings({ section, access, children }: { section: string; access: ReactNode; children: ReactNode }) {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const first = useFirstTab(section);
  const part = PARTS.find((key) => key === params.get("part")) ?? first(PARTS) ?? "access";
  return (
    <>
      <SectionTabs
        sub
        section={section}
        current={part}
        tabs={PARTS.map((key) => ({ key, text: t(`cards.settings_${key}`) }))}
        onSelect={(key) => setParams({ tab: "settings", part: key }, { replace: true })}
      />
      {part === "access" ? access : children}
    </>
  );
}
