import { useQuery } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, NavLink, Outlet, useLocation, useParams } from "react-router";

import { isMini, miniPage } from "../mini";

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
import { useCompanyTabs, useFinanceTabs, useProductivityTabs, useSectionHome } from "./CompanyTabs";
import { SearchBox } from "../pages/search/SearchPage";

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

/** Whether the employee has some right of tasks (§ 23.9; decision З-3). */
export function useMayTasks(): boolean {
  const { permissions } = useActive();
  return [...permissions].some((p) => p.startsWith("tasks."));
}

/** Whether some dashboard is open to the employee (§ 21.1.1): otherwise «Home» is the tasks. */
export function useMayDashboards(): boolean {
  const { permissions } = useActive();
  return permissions.has("reports.view") || permissions.has("warehouse.view");
}

export function companyPath(company: Pick<Company, "public_id">, path = ""): string {
  return `/c/${company.public_id}${path}`;
}

/** Sections of the company menu and when they appear (customer's decisions of 24.10.2026):
 * finance, clients and counterparties and settings always; orders after «Complete» of the setup
 * wizard; rental, service and warehouse with such a context in some location; the garage with
 * a rental or a service context. The order of items is a PROPOSAL. */
export function companyMenu(company: Company): [key: string, path: string, icon: IconName][] {
  const has = (type: ContextType) => contextAvailable(type) && company.context_types.includes(type);
  // Order of the customer (24.10.2026): company, dashboards, contacts, garage, finance, then
  // the directions and settings. Orders keep their place after finance (PROPOSAL).
  const items: [string, string, IconName, boolean][] = [
    // «Productivity» (named so on 16.11.2026; «Home» before) holds the dashboards and the tasks;
    // the orders are a tab of «Finance», the documents a tab of «Company». Order of the customer
    // (14.11.2026): productivity, finance, rental, service, warehouse, settings.
    ["productivity", "/dashboards", "productivity", true],
    ["finance", "/finance", "finance", true],
    // The garage and the contacts are tabs of «Company», the schedule and the trips are tabs of
    // «Rental» (customer's decision of 14.11.2026): the menu holds the daily work only.
    ["rentals", "/rentals", "rentals", has("rental")],
    ["service", "/service", "service", has("service")],
    ["warehouse", "/warehouse", "warehouse", has("warehouse")],
    ["settings", "/settings", "settings", true],
  ];
  return items.filter(([, , , visible]) => visible).map(([key, path, icon]) => [key, path, icon]);
}

/** Pages that belong to a section of the menu though their address is their own. */
const INSIDE: Record<string, string[]> = { productivity: ["/dashboards", "/tasks"], finance: ["/finance", "/orders"] };

/** The items of the menu. A section opens on its first tab in the user's order (customer's
 * decision of 17.11.2026): where every tab has a page of its own, the item leads to the page of
 * that tab; the other sections choose the tab themselves when their address names none. */
function Menu({ company }: { company: Company }) {
  const { t } = useTranslation();
  const homes: Record<string, string> = {
    productivity: useSectionHome("productivity", useProductivityTabs(), "/dashboards"),
    finance: useSectionHome("finance", useFinanceTabs(), "/finance"),
  };
  return (
    <>
      {companyMenu(company).map(([key, path, icon]) => (
        <RailLink
          key={key}
          to={companyPath(company, homes[key] ?? path)}
          icon={icon}
          label={t(`nav.${key}`)}
          also={(INSIDE[key] ?? []).map((part) => companyPath(company, part))}
        />
      ))}
    </>
  );
}

/** The company's logo leads to its card (decision of 24.10.2026) — to the first tab of
 * «Company» in the user's order. */
function CompanyLink({ company }: { company: Company }) {
  const home = useSectionHome("company", useCompanyTabs(), "/company");
  // The garage and the contacts are parts of «Company», so its item is lit there too.
  const { pathname } = useLocation();
  const inside = ["/company", "/vehicles", "/clients", "/documents"].some((part) => pathname.startsWith(companyPath(company, part)));
  return (
    <NavLink
      to={companyPath(company, home)}
      className={inside ? "rail-item rail-company active" : "rail-item rail-company"}
      aria-label={company.display_name}
      data-tip={company.display_name}
    >
      <Avatar file={company.logo} name={company.display_name} size={36} square />
    </NavLink>
  );
}

/** The line above a page of the window of the bot: the way back to its menu. */
function MiniBar({ cmp, rest }: { cmp: string; rest: string }) {
  const { t } = useTranslation();
  if (rest === "/app") return null;
  return (
    <p className="mini-bar">
      <NavLink to={`/c/${cmp}/app`}>← {t("miniapp.menu")}</NavLink>
    </p>
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

  const mini = isMini();
  const rest = useLocation().pathname.slice(`/c/${cmp}`.length).replace(/\/$/, "");
  const data = company.data?.company;
  const sidebar = data ? (
    // The menu knows the rights and the tabs of the sections, as the pages do.
    <CompanyContext.Provider value={company.data ?? null}>
      <CompanyLink company={data} />
      <Menu company={data} />
    </CompanyContext.Provider>
  ) : (
    // While the company loads, no menu: the default «My companies» would only flash.
    <></>
  );

  return (
    <AppShell user={user} sidebar={sidebar} search={data ? <SearchBox /> : null}>
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
            {mini ? <MiniBar cmp={cmp} rest={rest} /> : null}
            {mini && !miniPage(rest) ? (
              // The window opens only its own pages: anything else leads back to its menu. A
              // vehicle just made says so there.
              <Navigate
                to={`/c/${cmp}/app`}
                replace
                state={
                  /^\/vehicles\/[0-9a-f-]{36}$/.test(rest)
                    ? { done: "vehicle" }
                    : /^\/clients\/((investors|counterparties)\/)?[0-9a-f-]{36}$/.test(rest)
                      ? { done: "card" }
                      : null
                }
              />
            ) : (
              <Outlet />
            )}
          </ScopeContext.Provider>
        </CompanyContext.Provider>
      ) : null}
    </AppShell>
  );
}
