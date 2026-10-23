import { useQuery } from "@tanstack/react-query";
import { createContext, useContext } from "react";
import { useTranslation } from "react-i18next";
import { Link, NavLink, Outlet, useParams } from "react-router";

import { api, ApiError } from "../api/client";
import type { Company, User } from "../api/types";
import { Avatar } from "../components/images";
import { ErrorMessage, Loading } from "../components/ui";
import { AppShell } from "./AppShell";

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
 * company, finance, clients and counterparties and settings always; orders after «Complete» of
 * the setup wizard; rental, service and warehouse with such a context in some location; the
 * garage with a rental or a service context. The order of items is a PROPOSAL. */
export function companyMenu(company: Company): [key: string, path: string][] {
  const has = (type: string) => company.context_types.includes(type as never);
  const items: [string, string, boolean][] = [
    ["company", "/company", true],
    ["finance", "/finance", true],
    ["clients", "/clients", true],
    ["orders", "/orders", company.setup_completed_at !== null],
    ["rentals", "/rentals", has("rental")],
    ["service", "/service", has("service")],
    ["warehouse", "/warehouse", has("warehouse")],
    ["garage", "/vehicles", has("rental") || has("service")],
    ["settings", "/settings", true],
  ];
  return items.filter(([, , visible]) => visible).map(([key, path]) => [key, path]);
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

  const data = company.data?.company;
  const sidebar = data ? (
    <>
      <NavLink to={`/c/${cmp}/company`} className="side-company">
        <Avatar file={data.logo} name={data.display_name} size={36} square />
        <span>{data.display_name}</span>
      </NavLink>
      {companyMenu(data)
        .filter(([key]) => key !== "company")
        .map(([key, path]) => (
          <NavLink key={key} to={`/c/${cmp}${path}`}>
            {t(`nav.${key}`)}
          </NavLink>
        ))}
      <Link to="/companies" className="side-switch muted small">
        {t("nav.switch_company")}
      </Link>
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
        </div>
      ) : company.data ? (
        <CompanyContext.Provider value={company.data}>
          <Outlet />
        </CompanyContext.Provider>
      ) : null}
    </AppShell>
  );
}
