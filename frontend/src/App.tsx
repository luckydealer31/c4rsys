import { Navigate, Route, Routes } from "react-router";

import { useMe, useProfilePreferences } from "./auth";
import { CabinetApp, useSite } from "./cabinet/CabinetApp";
import { Loading } from "./components/ui";
import { AppShell } from "./layout/AppShell";
import { CompanyShell } from "./layout/CompanyShell";
import { ProfilePage } from "./pages/account/ProfilePage";
import { LoginPage } from "./pages/auth/LoginPage";
import { NotificationsPage } from "./pages/notifications/NotificationsPage";
import { SignByLinkPage } from "./pages/documents/Signing";
import { RegisterPage } from "./pages/auth/RegisterPage";
import { CompaniesPage } from "./pages/companies/CompaniesPage";
import { ClientPage, ClientsPage } from "./pages/clients/ClientsPage";
import { CounterpartyPage } from "./pages/clients/Counterparties";
import { LinkRequestsPage } from "./pages/company/LinkRequestsPage";
import { CompanyPage } from "./pages/company/CompanyPage";
import { ImportPage } from "./pages/imports/ImportPage";
import { ItemPage, NewItemPage } from "./pages/warehouse/ItemPage";
import { NewPurchasePage, PurchasePage } from "./pages/warehouse/Purchases";
import { InventoryPage } from "./pages/warehouse/Inventory";
import { NewOperationPage, OperationPage, RepackPage } from "./pages/warehouse/Operations";
import { ReceiptPage } from "./pages/warehouse/Receipts";
import { SaleStartPage } from "./pages/warehouse/Sales";
import { WarehousePage } from "./pages/warehouse/WarehousePage";
import { DashboardsPage } from "./pages/company/DashboardsPage";
import { ServiceCatalogPage } from "./pages/service/Catalog";
import { NewServiceOperationPage } from "./pages/service/NewOperation";
import { ServiceOperationPage } from "./pages/service/OperationPage";
import { ServicePage } from "./pages/service/ServicePage";
import { DocumentPage, DocumentsPage } from "./pages/documents/DocumentsPage";
import { TaskPage, TasksPage } from "./pages/tasks/TasksPage";
import { DocumentPrintPage, TemplatePage, TemplatesPage } from "./pages/documents/Templates";
import { DeliveryServicesPage } from "./pages/trips/DeliveryMoney";
import { NewTripPage, TripPage, TripsPage } from "./pages/trips/TripsPage";
import { FinancePage } from "./pages/finance/FinancePage";
import { OrderPage, OrdersPage } from "./pages/orders/OrdersPage";
import { BookingPage } from "./pages/rentals/Booking";
import { IssuePage } from "./pages/rentals/IssuePage";
import { RentalPage } from "./pages/rentals/RentalPage";
import { RentalsPage } from "./pages/rentals/RentalsPage";
import { ExtraServicePage, ExtraServicesPage, NewServiceOrderPage, ServiceLinePage } from "./pages/rentals/Services";
import { CompanyProfilePage } from "./pages/settings/CompanyProfilePage";
import { RentalPolicyPage } from "./pages/settings/RentalPolicyPage";
import { NotificationRulesPage } from "./pages/notifications/RulesPage";
import { LocationPage } from "./pages/settings/LocationsPage";
import { EmployeePage } from "./pages/settings/EmployeesPage";
import { ExportPage } from "./pages/settings/ExportPage";
import { SettingsPage } from "./pages/settings/SettingsPage";
import { SetupPage } from "./pages/setup/SetupPage";
import { TariffPage, TariffsPage } from "./pages/tariffs/TariffsPage";
import { EditVehiclePage, NewVehiclePage } from "./pages/vehicles/NewVehiclePage";
import { VehiclePage, VehiclesPage } from "./pages/vehicles/VehiclesPage";

export function App() {
  const me = useMe();
  const site = useSite();
  useProfilePreferences(me.data);

  if (me.isLoading || site.isLoading) {
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
        <Route path="/sign/:token" element={<SignByLinkPage />} />
        <Route path="*" element={<LoginPage />} />
      </Routes>
    );
  }

  // The domain of the personal cabinet shows only the cabinet (decision of 30.10.2026).
  if (site.data?.cabinet) return <CabinetApp user={user} />;

  return (
    <Routes>
      <Route path="/sign/:token" element={<SignByLinkPage />} />
      <Route
        path="/notifications"
        element={
          <AppShell user={user}>
            <NotificationsPage />
          </AppShell>
        }
      />
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
        <Route path="link-requests" element={<LinkRequestsPage />} />
        <Route path="link-requests/:id" element={<LinkRequestsPage />} />
        <Route path="dashboards" element={<DashboardsPage />} />
        <Route path="company/locations/:id" element={<LocationPage />} />
        <Route path="setup" element={<SetupPage />} />
        <Route path="rentals" element={<RentalsPage />} />
        <Route path="rentals/new" element={<IssuePage />} />
        <Route path="rentals/book" element={<BookingPage />} />
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
        <Route path="service" element={<ServicePage />} />
        <Route path="trips" element={<TripsPage />} />
        <Route path="tasks" element={<TasksPage />} />
        <Route path="tasks/:id" element={<TaskPage />} />
        <Route path="documents" element={<DocumentsPage />} />
        <Route path="documents/templates" element={<TemplatesPage />} />
        <Route path="documents/templates/:id" element={<TemplatePage />} />
        <Route path="documents/:id" element={<DocumentPage />} />
        <Route path="documents/:id/print/:version" element={<DocumentPrintPage />} />
        <Route path="trips/new" element={<NewTripPage />} />
        <Route path="trips/services" element={<DeliveryServicesPage />} />
        <Route path="trips/:id" element={<TripPage />} />
        <Route path="service/works" element={<ServiceCatalogPage />} />
        <Route path="service/operations/new" element={<NewServiceOperationPage />} />
        <Route path="service/operations/:id" element={<ServiceOperationPage />} />
        <Route path="warehouse" element={<WarehousePage />} />
        <Route path="warehouse/items/new" element={<NewItemPage />} />
        <Route path="warehouse/import" element={<ImportPage />} />
        <Route path="imports" element={<ImportPage />} />
        <Route path="warehouse/items/:id" element={<ItemPage />} />
        <Route path="warehouse/receipts/new" element={<ReceiptPage />} />
        <Route path="warehouse/receipts/:id" element={<ReceiptPage />} />
        <Route path="warehouse/operations/new" element={<NewOperationPage />} />
        <Route path="warehouse/operations/:id" element={<OperationPage />} />
        <Route path="warehouse/repack" element={<RepackPage />} />
        <Route path="warehouse/sale" element={<SaleStartPage />} />
        <Route path="warehouse/inventories/:id" element={<InventoryPage />} />
        <Route path="warehouse/purchases/new" element={<NewPurchasePage />} />
        <Route path="warehouse/purchases/:id" element={<PurchasePage />} />
        <Route path="finance" element={<FinancePage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="settings/company" element={<CompanyProfilePage />} />
        <Route path="settings/rental" element={<RentalPolicyPage />} />
        <Route path="settings/notifications" element={<NotificationRulesPage />} />
        <Route path="settings/export" element={<ExportPage />} />
        <Route path="settings/locations" element={<Navigate to="../company" replace />} />
        <Route path="rentals/tariffs" element={<TariffsPage />} />
        <Route path="rentals/services" element={<ExtraServicesPage />} />
        <Route path="rentals/services/order" element={<NewServiceOrderPage />} />
        <Route path="rentals/services/:id" element={<ExtraServicePage />} />
        <Route path="rentals/service-lines/:id" element={<ServiceLinePage />} />
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
