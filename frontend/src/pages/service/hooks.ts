import type { TFunction } from "i18next";

import { useApi } from "../../api/queries";
import type {
  Company,
  Counterparty,
  Location,
  QueueJob,
  ServiceOperation,
  ServicePost,
  ServiceRate,
  ServiceWork,
  SystemWork,
} from "../../api/types";
import { useCompany } from "../../layout/CompanyShell";
import { deviceTimeZone } from "../../time";

export const FINAL_STATES: ServiceOperation["state"][] = ["works_done", "cancelled", "terminated"];
export const HOLDING_STATES: ServiceOperation["state"][] = ["accepted", "in_progress"];

export function useSystemWorks() {
  const company = useCompany();
  return useApi<{ system_works: SystemWork[] }>(
    ["service-system-works", company.public_id],
    `/c/${company.public_id}/service/system-works`,
  );
}

export function useServiceWorks() {
  const company = useCompany();
  return useApi<{ works: ServiceWork[] }>(["service-works", company.public_id], `/c/${company.public_id}/service/works`);
}

export function useServiceRates() {
  const company = useCompany();
  return useApi<{ rates: ServiceRate[] }>(["service-rates", company.public_id], `/c/${company.public_id}/service/rates`);
}

export function useServicePosts() {
  const company = useCompany();
  return useApi<{ posts: ServicePost[] }>(["service-posts", company.public_id], `/c/${company.public_id}/service/posts`);
}

export function useServiceOperations(query = "") {
  const company = useCompany();
  return useApi<{ operations: ServiceOperation[] }>(
    ["service-operations", company.public_id, query],
    `/c/${company.public_id}/service/operations${query ? `?${query}` : ""}`,
  );
}

export function useServiceOperation(id: string | undefined) {
  const company = useCompany();
  return useApi<{ operation: ServiceOperation }>(
    ["service-operation", company.public_id, id],
    `/c/${company.public_id}/service/operations/${id}`,
    Boolean(id),
  );
}

export function useServiceQueue(enabled = true) {
  const company = useCompany();
  return useApi<{ jobs: QueueJob[] }>(["service-queue", company.public_id], `/c/${company.public_id}/service/queue`, enabled);
}

export function useExecutors(enabled = true) {
  const company = useCompany();
  return useApi<{ employees: { id: string; name: string }[] }>(
    ["service-executors", company.public_id],
    `/c/${company.public_id}/service/executors`,
    enabled,
  );
}

/** Active counterparties: external contractors of the service (§ 16.6.1). */
export function useContractors(enabled = true) {
  const company = useCompany();
  return useApi<{ counterparties: Counterparty[] }>(
    ["counterparties", company.public_id, "", false],
    `/c/${company.public_id}/counterparties?q=&state=active`,
    enabled,
  );
}

/** Locations with an enabled service context (§ 16.2.2). */
export function serviceLocations(locations: Location[] | undefined): Location[] {
  return (locations ?? []).filter(
    (l) => l.state !== "deactivated" && l.contexts.some((c) => c.type === "service" && c.state === "enabled"),
  );
}

export function zoneOf(location: Location | undefined, company: Company): string {
  return location?.timezone ?? company.timezone ?? deviceTimeZone();
}

/** The name of a work in the language of the interface: the company's own name. */
export function workTerms(work: Pick<ServiceWork, "pricing" | "price" | "hours" | "currency">, t: TFunction): string {
  if (work.pricing === "hours") {
    return work.hours ? t("service.terms_hours", { hours: Number(work.hours) }) : t("service.terms_hours_unknown");
  }
  return work.price !== null ? `${Number(work.price)} ${work.currency}` : t("service.price_unknown");
}

export function price(value: string | null, currency: string, t: TFunction): string {
  return value === null ? t("service.price_unknown") : `${Number(value)} ${currency}`;
}

/** Invalidates what a command on an operation changes. */
export const OPERATION_KEYS = [
  "service-operations",
  "service-operation",
  "service-queue",
  "orders",
  "order",
  "vehicles",
] as const;
