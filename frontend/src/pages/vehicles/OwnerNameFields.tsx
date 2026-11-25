import { useTranslation } from "react-i18next";

import { Field } from "../../components/ui";

/** A person is named by the last name, the first name and the middle name apart, so that a paper
 * can decline them; a company or an organization by its name (customer's remark of 25.11.2026). */
export type OwnerNames = {
  owner_name: string;
  owner_last_name: string;
  owner_first_name: string;
  owner_middle_name: string;
};

export const NO_OWNER_NAMES: OwnerNames = {
  owner_name: "",
  owner_last_name: "",
  owner_first_name: "",
  owner_middle_name: "",
};

/** An organization, or a profile given by its CMP ID. */
export function isCompanyOwner(kind: string, lookup: string): boolean {
  return kind === "organization" || (kind === "profile" && lookup.trim().toUpperCase().startsWith("CMP-"));
}

type Named = { name: string; last_name?: string; first_name?: string; middle_name?: string };

/** The names of a declared owner for a form. A name given before the parts were kept apart is
 * put into the last name, to be split by hand (agent's choice, not confirmed). */
export function ownerNamesOf(owner: Named | null | undefined, kind: string, lookup: string): OwnerNames {
  if (!owner) return NO_OWNER_NAMES;
  const parts = Boolean(owner.last_name || owner.first_name || owner.middle_name);
  if (isCompanyOwner(kind, lookup)) return { ...NO_OWNER_NAMES, owner_name: owner.name };
  return {
    owner_name: owner.name,
    owner_last_name: parts ? (owner.last_name ?? "") : owner.name,
    owner_first_name: owner.first_name ?? "",
    owner_middle_name: owner.middle_name ?? "",
  };
}

/** What the server is sent for the name. */
export function ownerNamesBody(kind: string, lookup: string, names: OwnerNames): Record<string, string> {
  if (isCompanyOwner(kind, lookup)) return { owner_name: names.owner_name };
  return {
    owner_last_name: names.owner_last_name,
    owner_first_name: names.owner_first_name,
    owner_middle_name: names.owner_middle_name,
  };
}

export function OwnerNameFields({
  kind,
  lookup,
  value,
  set,
}: {
  kind: string;
  lookup: string;
  value: OwnerNames;
  set: (change: Partial<OwnerNames>) => void;
}) {
  const { t } = useTranslation();
  if (isCompanyOwner(kind, lookup)) {
    return (
      <Field label={t(kind === "organization" ? "vehicles.owner_name_organization" : "vehicles.owner_name_company")}>
        <input value={value.owner_name} onChange={(e) => set({ owner_name: e.target.value })} />
      </Field>
    );
  }
  return (
    <>
      <Field label={t("vehicles.owner_last_name")}>
        <input value={value.owner_last_name} onChange={(e) => set({ owner_last_name: e.target.value })} />
      </Field>
      <Field label={t("vehicles.owner_first_name")}>
        <input value={value.owner_first_name} onChange={(e) => set({ owner_first_name: e.target.value })} />
      </Field>
      <Field label={t("vehicles.owner_middle_name")}>
        <input value={value.owner_middle_name} onChange={(e) => set({ owner_middle_name: e.target.value })} />
      </Field>
    </>
  );
}
