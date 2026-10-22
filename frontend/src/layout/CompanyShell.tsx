import { useQuery } from "@tanstack/react-query";
import { createContext, useContext } from "react";
import { useTranslation } from "react-i18next";
import { NavLink, Outlet, useParams } from "react-router";

import { api, ApiError } from "../api/client";
import type { Company, User } from "../api/types";
import { ErrorMessage, Loading } from "../components/ui";
import { AccountBar } from "./AccountBar";

const CompanyContext = createContext<Company | null>(null);

/** The active company of the page. Operations happen only in its context (§ 3.3.8). */
export function useCompany(): Company {
  const company = useContext(CompanyContext);
  if (!company) throw new Error("useCompany outside of a company page");
  return company;
}

export function companyPath(company: Company, path = ""): string {
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
    queryFn: async () => (await api<{ company: Company }>(`/c/${cmp}`)).company,
    retry: false,
  });

  return (
    <>
      <AccountBar user={user}>
        {company.data ? (
          <>
            <span className="company">{company.data.display_name}</span>
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
