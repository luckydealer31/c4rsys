import { useQuery } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Outlet, useNavigate, useParams } from "react-router";

import { api, ApiError } from "../api/client";
import type { Company, User } from "../api/types";
import { Icon, type IconName } from "../components/icons";
import { Avatar } from "../components/images";
import { useDismiss } from "../components/useDismiss";
import { ErrorMessage, Loading } from "../components/ui";
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
export function companyMenu(company: Company): [key: string, path: string, icon: IconName][] {
  const has = (type: string) => company.context_types.includes(type as never);
  const items: [string, string, IconName, boolean][] = [
    ["finance", "/finance", "finance", true],
    ["clients", "/clients", "people", true],
    ["orders", "/orders", "orders", company.setup_completed_at !== null],
    ["rentals", "/rentals", "rentals", has("rental")],
    ["service", "/service", "service", has("service")],
    ["warehouse", "/warehouse", "warehouse", has("warehouse")],
    ["garage", "/vehicles", "garage", has("rental") || has("service")],
    ["settings", "/settings", "settings", true],
  ];
  return items.filter(([, , , visible]) => visible).map(([key, path, icon]) => [key, path, icon]);
}

/** The company's own menu, opened by its logo: its card, its settings and leaving it for «My
 * companies» (decision of 24.10.2026). */
function CompanyMenu({ company }: { company: Company }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const close = useCallback(() => setOpen(false), []);
  useDismiss(open, close, root);
  const go = (path: string) => {
    setOpen(false);
    navigate(path);
  };
  return (
    <div className="company-menu" ref={root}>
      <button
        type="button"
        className="rail-item rail-company"
        aria-expanded={open}
        aria-label={company.display_name}
        data-tip={company.display_name}
        onClick={() => setOpen(!open)}
      >
        <Avatar file={company.logo} name={company.display_name} size={36} square />
      </button>
      {open ? (
        <div className="popup company-popup" role="menu">
          <div className="popup-head">
            <Avatar file={company.logo} name={company.display_name} size={40} square />
            <div>
              <strong>{company.display_name}</strong>
              <div className="muted small">{company.public_id}</div>
            </div>
          </div>
          <button type="button" className="menu-row" onClick={() => go(companyPath(company, "/company"))}>
            <Icon name="card" /> {t("company.card")}
          </button>
          <button
            type="button"
            className="menu-row"
            onClick={() => go(companyPath(company, "/settings/company"))}
          >
            <Icon name="settings" /> {t("company.settings")}
          </button>
          <button type="button" className="menu-row" onClick={() => go("/companies")}>
            <Icon name="exit" /> {t("company.leave")}
          </button>
        </div>
      ) : null}
    </div>
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

  const data = company.data?.company;
  const sidebar = data ? (
    <>
      <CompanyMenu company={data} />
      {companyMenu(data).map(([key, path, icon]) => (
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
        </div>
      ) : company.data ? (
        <CompanyContext.Provider value={company.data}>
          <Outlet />
        </CompanyContext.Provider>
      ) : null}
    </AppShell>
  );
}
