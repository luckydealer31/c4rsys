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
import { CounterpartyPage } from "./pages/clients/Counterparties";
import { CompanyPage } from "./pages/company/CompanyPage";
import { ImportPage } from "./pages/warehouse/ImportPage";
import { ItemPage, NewItemPage } from "./pages/warehouse/ItemPage";
import { NewPurchasePage, PurchasePage } from "./pages/warehouse/Purchases";
import { InventoryPage } from "./pages/warehouse/Inventory";
import { NewOperationPage, OperationPage, RepackPage } from "./pages/warehouse/Operations";
import { ReceiptPage } from "./pages/warehouse/Receipts";
import { WarehousePage } from "./pages/warehouse/WarehousePage";
import { DashboardsPage } from "./pages/company/DashboardsPage";
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
import { EditVehiclePage, NewVehiclePage } from "./pages/vehicles/NewVehiclePage";
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
        <Route path="dashboards" element={<DashboardsPage />} />
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
        <Route path="vehicles/:id/edit" element={<EditVehiclePage />} />
        <Route path="clients" element={<ClientsPage />} />
        <Route path="clients/counterparties/:id" element={<CounterpartyPage />} />
        <Route path="clients/:id" element={<ClientPage />} />
        <Route path="service" element={<ContextSectionPage type="service" />} />
        <Route path="warehouse" element={<WarehousePage />} />
        <Route path="warehouse/items/new" element={<NewItemPage />} />
        <Route path="warehouse/import" element={<ImportPage />} />
        <Route path="warehouse/items/:id" element={<ItemPage />} />
        <Route path="warehouse/receipts/new" element={<ReceiptPage />} />
        <Route path="warehouse/receipts/:id" element={<ReceiptPage />} />
        <Route path="warehouse/operations/new" element={<NewOperationPage />} />
        <Route path="warehouse/operations/:id" element={<OperationPage />} />
        <Route path="warehouse/repack" element={<RepackPage />} />
        <Route path="warehouse/inventories/:id" element={<InventoryPage />} />
        <Route path="warehouse/purchases/new" element={<NewPurchasePage />} />
        <Route path="warehouse/purchases/:id" element={<PurchasePage />} />
        <Route path="finance" element={<FinancePage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="settings/company" element={<CompanyProfilePage />} />
        <Route path="settings/locations" element={<Navigate to="../company" replace />} />
        <Route path="rentals/tariffs" element={<TariffsPage />} />
        <Route path="rentals/tariffs/:id" element={<TariffPage />} />
        <Route path="settings/tariffs" element={<Navigate to="../rentals/tariffs" replace />} />
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
