import { Navigate, Route, Routes } from "react-router";

import { useMe, useProfilePreferences } from "./auth";
import { Loading } from "./components/ui";
import { AppShell } from "./layout/AppShell";
import { CompanyShell } from "./layout/CompanyShell";
import { ProfilePage } from "./pages/account/ProfilePage";
import { LoginPage } from "./pages/auth/LoginPage";
import { RegisterPage } from "./pages/auth/RegisterPage";
import { CompaniesPage } from "./pages/companies/CompaniesPage";
import { ClientPage, ClientsPage } from "./pages/clients/ClientsPage";
import { CompanyPage } from "./pages/company/CompanyPage";
import { ContextSectionPage } from "./pages/contexts/ContextLocations";
import { FinancePage } from "./pages/finance/FinancePage";
import { OrderPage, OrdersPage } from "./pages/orders/OrdersPage";
import { IssuePage } from "./pages/rentals/IssuePage";
import { RentalPage } from "./pages/rentals/RentalPage";
import { RentalsPage } from "./pages/rentals/RentalsPage";
import { CompanyProfilePage } from "./pages/settings/CompanyProfilePage";
import { LocationPage } from "./pages/settings/LocationsPage";
import { EmployeePage } from "./pages/settings/EmployeesPage";
import { SettingsPage } from "./pages/settings/SettingsPage";
import { SetupPage } from "./pages/setup/SetupPage";
import { TariffPage, TariffsPage } from "./pages/tariffs/TariffsPage";
import { NewVehiclePage } from "./pages/vehicles/NewVehiclePage";
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
          <AppShell user={user}>
            <ProfilePage user={user} />
          </AppShell>
        }
      />
      <Route
        path="/companies"
        element={
          <AppShell user={user}>
            <CompaniesPage user={user} />
          </AppShell>
        }
      />
      <Route
        path="/invitations"
        element={
          <Navigate to="/companies" replace />
        }
      />
      <Route path="/c/:cmp" element={<CompanyShell user={user} />}>
        <Route index element={<Navigate to="company" replace />} />
        <Route path="company" element={<CompanyPage />} />
        <Route path="company/locations/:id" element={<LocationPage />} />
        <Route path="setup" element={<SetupPage />} />
        <Route path="rentals" element={<RentalsPage />} />
        <Route path="rentals/new" element={<IssuePage />} />
        <Route path="rentals/:id" element={<RentalPage />} />
        <Route path="orders" element={<OrdersPage />} />
        <Route path="orders/:id" element={<OrderPage />} />
        <Route path="vehicles" element={<VehiclesPage />} />
        <Route path="vehicles/new" element={<NewVehiclePage />} />
        <Route path="vehicles/:id" element={<VehiclePage />} />
        <Route path="clients" element={<ClientsPage />} />
        <Route path="clients/:id" element={<ClientPage />} />
        <Route path="service" element={<ContextSectionPage type="service" />} />
        <Route path="warehouse" element={<ContextSectionPage type="warehouse" />} />
        <Route path="finance" element={<FinancePage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="settings/company" element={<CompanyProfilePage />} />
        <Route path="settings/locations" element={<Navigate to="../company" replace />} />
        <Route path="settings/tariffs" element={<TariffsPage />} />
        <Route path="settings/tariffs/:id" element={<TariffPage />} />
        <Route path="clients/employees/:id" element={<EmployeePage />} />
        <Route path="settings/employees" element={<Navigate to="../clients?tab=employees" replace />} />
      </Route>
      <Route
        path="*"
        element={<Navigate to={user.has_full_name ? "/companies" : "/profile"} replace />}
      />
    </Routes>
  );
}
