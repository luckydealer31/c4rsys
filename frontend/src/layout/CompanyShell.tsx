import { useQuery } from "@tanstack/react-query";
import { createContext, useContext } from "react";
import { useTranslation } from "react-i18next";
import { NavLink, Outlet, useParams } from "react-router";

import { api, ApiError } from "../api/client";
import type { Company, User } from "../api/types";
import { ErrorMessage, Loading } from "../components/ui";
import { AccountBar } from "./AccountBar";

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

const MENU = [
  ["rentals", "/rentals"],
  ["orders", "/orders"],
  ["garage", "/vehicles"],
  ["clients", "/clients"],
  ["finance", "/finance"],
  ["settings", "/settings"],
] as const;

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

  return (
    <>
      <AccountBar user={user}>
        {company.data ? (
          <>
            <span className="company">{company.data.company.display_name}</span>
            <nav className="nav menu">
              {MENU.map(([key, path]) => (
                <NavLink key={key} to={`/c/${cmp}${path}`}>
                  {t(`nav.${key}`)}
                </NavLink>
              ))}
            </nav>
          </>
        ) : null}
      </AccountBar>
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
    </>
  );
}
