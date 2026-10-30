import { useQuery } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { NavLink, Outlet, useParams } from "react-router";

import { api, ApiError } from "../api/client";
import type { Company, ContextType, User } from "../api/types";
import type { IconName } from "../components/icons";
import { Avatar } from "../components/images";
import { ErrorMessage, Loading } from "../components/ui";
import { BlockedNotice } from "../pages/auth/LoginPage";
import { contextAvailable } from "../contexts";
import { ScopeContext, useAutoSync, useOfflineDatasets } from "../offline/hooks";
import { rememberCompany } from "../offline/profile";
import { AppShell, RailLink } from "./AppShell";

type ActiveCompany = { company: Company; permissions: Set<string> };

const CompanyContext = createContext<ActiveCompany | null>(null);

function useActive(): ActiveCompany {
  const active = useContext(CompanyContext);
  if (!active) throw new Error("Used outside of a company page");
  return active;
}

/** The active company of the page. Operations happen only in its context (§ 3.3.8). */
export function useCompany(): Company {
  return useActive().company;
}

/** Whether the employee has a permission somewhere in the company. Only hides actions in the
 * interface; the server checks every action (STAGE1_SCREENS). */
export function useCan(): (permission: string) => boolean {
  const { permissions } = useActive();
  return (permission) => permissions.has(permission);
}

export function companyPath(company: Pick<Company, "public_id">, path = ""): string {
  return `/c/${company.public_id}${path}`;
}

/** Sections of the company menu and when they appear (customer's decisions of 24.10.2026):
 * finance, clients and counterparties and settings always; orders after «Complete» of the setup
 * wizard; rental, service and warehouse with such a context in some location; the garage with
 * a rental or a service context. The order of items is a PROPOSAL. */
export function companyMenu(
  company: Company,
  permissions: Set<string> = new Set(),
): [key: string, path: string, icon: IconName][] {
  const has = (type: ContextType) => contextAvailable(type) && company.context_types.includes(type);
  // Order of the customer (24.10.2026): company, dashboards, contacts, garage, finance, then
  // the directions and settings. Orders keep their place after finance (PROPOSAL).
  const items: [string, string, IconName, boolean][] = [
    ["dashboards", "/dashboards", "dashboards", true],
    ["clients", "/clients", "people", true],
    ["garage", "/vehicles", "garage", has("rental") || has("service")],
    // Block 8 (ТЗ гл. 10): inspections for whoever has a right of inspections in a company with
    // a rental or a service context. PROPOSAL: the place of the item in the menu.
    [
      "inspections",
      "/inspections",
      "inspections",
      (has("rental") || has("service")) && [...permissions].some((p) => p.startsWith("inspection.")),
    ],
    ["finance", "/finance", "finance", true],
    ["orders", "/orders", "orders", company.setup_completed_at !== null],
    ["rentals", "/rentals", "rentals", has("rental")],
    ["service", "/service", "service", has("service")],
    ["warehouse", "/warehouse", "warehouse", has("warehouse")],
    // Decision Д-10: trips for whoever plans, takes or drives them.
    ["trips", "/trips", "trips", [...permissions].some((p) => p.startsWith("delivery."))],
    // Decision Р-6: the registry for whoever may see or add documents.
    ["documents", "/documents", "documents", permissions.has("documents.view") || permissions.has("documents.upload")],
    // Decision З-3: work tasks for whoever has a right of § 23.9.
    ["tasks", "/tasks", "tasks", [...permissions].some((p) => p.startsWith("tasks."))],
    ["settings", "/settings", "settings", true],
  ];
  return items.filter(([, , , visible]) => visible).map(([key, path, icon]) => [key, path, icon]);
}

/** The company's logo leads straight to its card (decision of 24.10.2026). */
function CompanyLink({ company }: { company: Company }) {
  return (
    <NavLink
      to={companyPath(company, "/company")}
      className="rail-item rail-company"
      aria-label={company.display_name}
      data-tip={company.display_name}
    >
      <Avatar file={company.logo} name={company.display_name} size={36} square />
    </NavLink>
  );
}

export function CompanyShell({ user }: { user: User }) {
  const { t } = useTranslation();
  const { cmp = "" } = useParams();
  const company = useQuery({
    queryKey: ["company", cmp],
    queryFn: async () => {
      const body = await api<{ company: Company; permissions: string[] }>(`/c/${cmp}`);
      return { company: body.company, permissions: new Set(body.permissions) };
    },
    retry: false,
  });

  // Local drafts and the lists for an offline start belong to this account and company
  // (§ 10.4.3–10.4.4); waiting drafts go when the connection returns (§ 10.4.2).
  const scope = useMemo(() => ({ userId: user.public_id, companyId: cmp }), [user.public_id, cmp]);
  const canInspect = company.data?.permissions.has("inspection.create") ?? false;
  useOfflineDatasets(scope, canInspect);
  useAutoSync(scope);
  useEffect(() => {
    if (company.data) rememberCompany(user.public_id, company.data.company, canInspect);
  }, [company.data, user.public_id, canInspect]);

  const data = company.data?.company;
  const sidebar = data ? (
    <>
      <CompanyLink company={data} />
      {companyMenu(data, company.data?.permissions).map(([key, path, icon]) => (
        <RailLink key={key} to={`/c/${cmp}${path}`} icon={icon} label={t(`nav.${key}`)} />
      ))}
    </>
  ) : (
    // While the company loads, no menu: the default «My companies» would only flash.
    <></>
  );

  return (
    <AppShell user={user} sidebar={sidebar}>
      {company.isLoading ? (
        <div className="page">
          <Loading />
        </div>
      ) : company.error ? (
        <div className="page">
          <ErrorMessage error={company.error as ApiError} />
          {/* § 24.2.2: a company blocked by c4rsys — the support contact. */}
          <BlockedNotice error={company.error as ApiError} />
        </div>
      ) : company.data ? (
        <CompanyContext.Provider value={company.data}>
          <ScopeContext.Provider value={scope}>
            {company.data.company.deactivated_at ? (
              // § 3.6.1: a deactivated company starts no new activity; settlements, history and
              // export stay.
              <div className="alert warn deactivated-banner" role="status">
                {t("company_state.banner")}{" "}
                <NavLink to={`/c/${cmp}/settings/state`}>{t("company_state.banner_link")}</NavLink>
              </div>
            ) : null}
            <Outlet />
          </ScopeContext.Provider>
        </CompanyContext.Provider>
      ) : null}
    </AppShell>
  );
}
