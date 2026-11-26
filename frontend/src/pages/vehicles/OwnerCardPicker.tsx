import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { api } from "../../api/client";
import type { Client, Counterparty, Vehicle } from "../../api/types";
import { Field, IconButton } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { ContactsEditor, contactsOf, phoneAndEmail, type ContactRow } from "../clients/ContactsEditor";

/** The owner of a managed vehicle is an investor of the company (customer's decision of
 * 26.11.2026, in place of the counterparty of 25.11.2026; the server still names the field
 * «counterparty»: the cards of both lists are of one shape), of a client vehicle a client. The owner is a person or a company, entered with the
 * usual data of a card; while it is typed, the cards of the list that match — by the last name
 * alone, the name, the phone, the email, the company name — are offered, and one of them is
 * taken with a press (customer's remark of 25.11.2026: no search of its own, no ID here; the ID
 * of the profile to link is given in the card of the counterparty). */
export type OwnerValue = {
  party: "person" | "company" | "none";
  card_id: string;
  card_name: string;
  card_contacts: string;
  last_name: string;
  first_name: string;
  middle_name: string;
  company_name: string;
  contact_person: string;
  contacts: ContactRow[];
  address: string;
};

export const NO_OWNER: OwnerValue = {
  party: "none",
  card_id: "",
  card_name: "",
  card_contacts: "",
  last_name: "",
  first_name: "",
  middle_name: "",
  company_name: "",
  contact_person: "",
  contacts: [{ choice: "", value: "" }],
  address: "",
};

type Card = { id: string; kind: "person" | "company"; name: string; contacts: string };

/** The owner of a saved vehicle for its form. An owner described before the cards (by hand or
 * by a profile) is put into the fields of a new card, to be saved as a card (agent's choice). */
export function ownerOf(vehicle: Vehicle): OwnerValue {
  const o = vehicle.owner;
  if (!o || o.kind === "none" || o.kind === "company") return NO_OWNER;
  if (o.counterparty) {
    return { ...NO_OWNER, party: o.counterparty.kind, card_id: o.counterparty.id, card_name: o.counterparty.name };
  }
  if (o.client) {
    return { ...NO_OWNER, party: o.client.kind ?? "person", card_id: o.client.id, card_name: o.client.name };
  }
  const company = o.kind === "organization" || o.lookup.toUpperCase().startsWith("CMP-");
  const parts = Boolean(o.last_name || o.first_name || o.middle_name);
  const contact = o.contact ?? "";
  const lookup = o.lookup.toUpperCase().startsWith("USR-") || o.lookup.toUpperCase().startsWith("CMP-") ? "" : o.lookup;
  const email = [contact, lookup].find((c) => c.includes("@")) ?? "";
  const phone = [contact, lookup].find((c) => c && !c.includes("@")) ?? "";
  return {
    ...NO_OWNER,
    party: company ? "company" : "person",
    company_name: company ? o.name : "",
    last_name: company ? "" : parts ? (o.last_name ?? "") : o.name,
    first_name: company ? "" : (o.first_name ?? ""),
    middle_name: company ? "" : (o.middle_name ?? ""),
    contacts: [
      ...(phone ? [{ choice: "phone", value: phone }] : []),
      ...(email ? [{ choice: "email", value: email }] : []),
      ...(phone || email ? [] : [{ choice: "", value: "" }]),
    ],
    address: o.address ?? "",
  };
}

/** The owner of a saved vehicle sent back as he is: the same card, or the same data given
 * before the cards. Used when the owner may not be changed. */
export function sameOwnerBody(o: NonNullable<Vehicle["owner"]>): Record<string, unknown> {
  if (o.counterparty) return { owner_kind: "counterparty", owner_counterparty_id: o.counterparty.id };
  if (o.client) return { owner_kind: "client", owner_client_id: o.client.id };
  return {
    owner_kind: o.kind,
    owner_lookup: o.lookup,
    owner_name: o.name,
    owner_last_name: o.last_name ?? "",
    owner_first_name: o.first_name ?? "",
    owner_middle_name: o.middle_name ?? "",
    owner_contact: o.contact,
    owner_address: o.address ?? "",
  };
}

/** What the server is sent for the owner. */
export function ownerBody(relation: string, v: OwnerValue): Record<string, unknown> {
  if (v.party === "none") return { owner_kind: "none" };
  const client = relation === "client";
  const body: Record<string, unknown> = { owner_kind: client ? "client" : "counterparty" };
  if (v.card_id) {
    body[client ? "owner_client_id" : "owner_counterparty_id"] = v.card_id;
    return body;
  }
  const person = v.party === "person";
  body.owner_card = {
    kind: v.party,
    ...(person
      ? { last_name: v.last_name.trim(), first_name: v.first_name.trim(), middle_name: v.middle_name.trim() }
      : { company_name: v.company_name.trim(), contact_person: v.contact_person.trim() }),
    address: v.address.trim(),
    contacts: contactsOf(v.contacts),
  };
  return body;
}

function useDebounced<T>(value: T): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), 400);
    return () => window.clearTimeout(timer);
  }, [value]);
  return debounced;
}

function asCard(item: Client | Counterparty): Card {
  const name =
    item.kind === "company"
      ? item.company_name
      : [item.last_name, item.first_name, item.middle_name].filter(Boolean).join(" ");
  return {
    id: item.id,
    kind: item.kind,
    name: name || ("name" in item ? item.name : ""),
    contacts: (item.contacts ?? []).map((c) => c.value).join(", "),
  };
}

export function OwnerCardPicker({
  relation,
  value,
  set,
  required = false,
}: {
  relation: "managed" | "client";
  value: OwnerValue;
  set: (change: Partial<OwnerValue>) => void;
  /** A new vehicle in the management has its owner — an investor (customer's decision of
   * 26.11.2026): «not given» is not offered. */
  required?: boolean;
}) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const client = relation === "client";
  const list = client ? "clients" : "investors";
  const person = value.party === "person";
  const draft = useDebounced({
    first_name: person ? value.first_name.trim() : "",
    last_name: person ? value.last_name.trim() : "",
    company_name: person ? "" : value.company_name.trim(),
    ...phoneAndEmail(value.contacts),
  });
  const filled = Boolean(
    draft.last_name || draft.company_name.length >= 3 || draft.phone.length >= 5 || draft.email.includes("@"),
  );
  // The matches in the list are offered as the owner is typed (customer's remark of 25.11.2026).
  const matches = useQuery({
    queryKey: [`${list}-owner-matches`, company.public_id, draft],
    enabled: value.party !== "none" && !value.card_id && filled && can(client ? "client.find" : "investor.view"),
    retry: false,
    queryFn: async () => {
      const body: Record<string, unknown> = { ...Object.fromEntries(Object.entries(draft).filter(([, v]) => v)), loose: true };
      const answer = await api<{ matches: { client?: Client; counterparty?: Counterparty; matched: string[] }[] }>(
        `/c/${company.public_id}/${list}/matches`,
        { method: "POST", body },
      );
      return answer.matches
        .map((m) => ({ card: asCard((m.client ?? m.counterparty) as Client | Counterparty), matched: m.matched }))
        .filter((m) => m.card.kind === value.party);
    },
  });

  const missing = required && value.party === "none";
  useEffect(() => {
    if (missing) set({ ...NO_OWNER, party: "person" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [missing]);

  const choose = (card: Card) =>
    set({ ...NO_OWNER, party: card.kind, card_id: card.id, card_name: card.name, card_contacts: card.contacts });
  const path = (id: string) => companyPath(company, client ? `/clients/${id}` : `/clients/investors/${id}`);
  const input = (key: Exclude<keyof OwnerValue, "contacts" | "party">, label: string) => (
    <Field label={label}>
      <input value={value[key]} onChange={(e) => set({ [key]: e.target.value })} />
    </Field>
  );

  return (
    <div data-testid="owner-picker">
      <div className="row short-fields">
        <Field label={t("vehicles.owner_kind")} info={t(client ? "vehicles.owner_client_hint" : "vehicles.owner_managed_hint")}>
          <select value={value.party} onChange={(e) => set({ ...NO_OWNER, party: e.target.value as OwnerValue["party"] })}>
            <option value="person">{t("vehicles.owner_party_person")}</option>
            <option value="company">{t("vehicles.owner_party_company")}</option>
            {required ? null : <option value="none">{t("vehicles.owner_kind_none")}</option>}
          </select>
        </Field>
        {value.card_id ? (
          <Field label={t(client ? "vehicles.owner_card_client" : "vehicles.owner_card_counterparty")}>
            <span className="row" style={{ alignItems: "center", gap: 6 }}>
              {can(client ? "client.list" : "investor.view") ? (
                <Link to={path(value.card_id)}>{value.card_name}</Link>
              ) : (
                <span>{value.card_name}</span>
              )}
              {value.card_contacts ? <span className="muted small">&nbsp;· {value.card_contacts}&nbsp;</span> : null}
              <IconButton
                icon="close"
                label={t("vehicles.owner_card_other")}
                onClick={() => set({ ...NO_OWNER, party: value.party })}
              />
            </span>
          </Field>
        ) : null}
      </div>
      {value.party !== "none" && !value.card_id ? (
        <>
          <div className="row short-fields">
            {person ? (
              <>
                {input("last_name", t("vehicles.owner_last_name"))}
                {input("first_name", t("vehicles.owner_first_name"))}
                {input("middle_name", t("vehicles.owner_middle_name"))}
              </>
            ) : (
              <>
                {input("company_name", t("vehicles.owner_name_company"))}
                {input("contact_person", t("vehicles.owner_contact_person"))}
              </>
            )}
            {input("address", t("vehicles.owner_address"))}
          </div>
          <ContactsEditor rows={value.contacts} onChange={(contacts) => set({ contacts })} />
          {(matches.data ?? []).length > 0 ? (
            <div className="alert warn" data-testid="owner-matches">
              <strong>{t("vehicles.owner_matches_title")}</strong>
              <ul>
                {(matches.data ?? []).map(({ card, matched }) => (
                  <li key={card.id}>
                    <button type="button" className="secondary" onClick={() => choose(card)}>
                      {t("vehicles.owner_use_existing", { name: card.name })}
                    </button>{" "}
                    <span className="small">
                      ({matched.map((m) => t(`clients.matched_${m}`)).join(", ")}
                      {card.contacts ? `; ${card.contacts}` : ""})
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
