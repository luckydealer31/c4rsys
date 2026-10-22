import { Navigate, Route, Routes } from "react-router";

import { useMe, useProfilePreferences } from "./auth";
import { Loading } from "./components/ui";
import { AccountBar } from "./layout/AccountBar";
import { CompanyShell } from "./layout/CompanyShell";
import { InvitationsPage } from "./pages/account/InvitationsPage";
import { ProfilePage } from "./pages/account/ProfilePage";
import { LoginPage } from "./pages/auth/LoginPage";
import { RegisterPage } from "./pages/auth/RegisterPage";
import { CompaniesPage } from "./pages/companies/CompaniesPage";
import { ClientPage, ClientsPage } from "./pages/clients/ClientsPage";
import { FinancePage } from "./pages/finance/FinancePage";
import { Placeholder } from "./pages/Placeholder";
import { CompanyProfilePage } from "./pages/settings/CompanyProfilePage";
import { EmployeePage, EmployeesPage } from "./pages/settings/EmployeesPage";
import { LocationsPage } from "./pages/settings/LocationsPage";
import { SettingsPage } from "./pages/settings/SettingsPage";
import { SetupPage } from "./pages/setup/SetupPage";
import { TariffPage, TariffsPage } from "./pages/tariffs/TariffsPage";
import { VehiclePage, VehiclesPage } from "./pages/vehicles/VehiclesPage";

export function App() {
  const me = useMe();
  useProfilePreferences(me.data);

  if (me.isLoading) {
    return (
      <div className="narrow">
        <Loading />
      </div>
    );
  }
  const user = me.data ?? null;

  if (!user) {
    return (
      <Routes>
        <Route path="/register" element={<RegisterPage />} />
        <Route path="*" element={<LoginPage />} />
      </Routes>
    );
  }

  return (
    <Routes>
      <Route
        path="/profile"
        element={
          <>
            <AccountBar user={user} />
            <ProfilePage user={user} />
          </>
        }
      />
      <Route
        path="/companies"
        element={
          <>
            <AccountBar user={user} />
            <CompaniesPage user={user} />
          </>
        }
      />
      <Route
        path="/invitations"
        element={
          <>
            <AccountBar user={user} />
            <InvitationsPage user={user} />
          </>
        }
      />
      <Route path="/c/:cmp" element={<CompanyShell user={user} />}>
        <Route index element={<Navigate to="rentals" replace />} />
        <Route path="setup" element={<SetupPage />} />
        <Route path="rentals" element={<Placeholder section="rentals" />} />
        <Route path="orders" element={<Placeholder section="orders" />} />
        <Route path="vehicles" element={<VehiclesPage />} />
        <Route path="vehicles/:id" element={<VehiclePage />} />
        <Route path="clients" element={<ClientsPage />} />
        <Route path="clients/:id" element={<ClientPage />} />
        <Route path="finance" element={<FinancePage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="settings/company" element={<CompanyProfilePage />} />
        <Route path="settings/locations" element={<LocationsPage />} />
        <Route path="settings/tariffs" element={<TariffsPage />} />
        <Route path="settings/tariffs/:id" element={<TariffPage />} />
        <Route path="settings/employees" element={<EmployeesPage />} />
        <Route path="settings/employees/:id" element={<EmployeePage />} />
      </Route>
      <Route
        path="*"
        element={<Navigate to={user.has_full_name ? "/companies" : "/profile"} replace />}
      />
    </Routes>
  );
}
