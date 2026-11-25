/** The lists of cards of a company (§ 6.1.7; customer's decision of 26.11.2026): the clients, the
 * counterparties and the investors — those who gave vehicles into the management or put in
 * money. Each list has an address, a tab and rights of its own; the rights are named
 * `<list>.view`, `<list>.edit` and so on. */
export type CardType = "client" | "counterparty" | "investor";

/** The cards of the counterparties and of the investors are of one shape. */
export type PartyList = "counterparty" | "investor";

const API: Record<CardType, string> = { client: "clients", counterparty: "counterparties", investor: "investors" };

/** The part of the address of the API: `/c/<company>/<list>`. */
export function apiList(cardType: CardType): string {
  return API[cardType];
}

/** The page of a card inside the company. */
export function cardPath(cardType: CardType, id: string): string {
  return cardType === "client" ? `/clients/${id}` : `/clients/${API[cardType]}/${id}`;
}

/** The tab of «Clients and counterparties» with the list. */
export function listPath(cardType: CardType): string {
  return `/clients?tab=${API[cardType]}`;
}
