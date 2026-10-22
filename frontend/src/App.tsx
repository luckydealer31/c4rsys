import { Navigate, Route, Routes } from "react-router";

import { useMe, useProfilePreferences } from "./auth";
import { Loading } from "./components/ui";
import { AccountBar } from "./layout/AccountBar";
import { CompanyShell } from "./layout/CompanyShell";
import { ProfilePage } from "./pages/account/ProfilePage";
import { LoginPage } from "./pages/auth/LoginPage";
import { RegisterPage } from "./pages/auth/RegisterPage";
import { CompaniesPage } from "./pages/companies/CompaniesPage";
import { Placeholder } from "./pages/Placeholder";

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
      <Route path="/c/:cmp" element={<CompanyShell user={user} />}>
        <Route index element={<Navigate to="rentals" replace />} />
        <Route path="setup" element={<Placeholder section="setup" />} />
        <Route path="rentals" element={<Placeholder section="rentals" />} />
        <Route path="orders" element={<Placeholder section="orders" />} />
        <Route path="vehicles" element={<Placeholder section="garage" />} />
        <Route path="clients" element={<Placeholder section="clients" />} />
        <Route path="finance" element={<Placeholder section="finance" />} />
        <Route path="settings" element={<Placeholder section="settings" />} />
      </Route>
      <Route
        path="*"
        element={<Navigate to={user.has_full_name ? "/companies" : "/profile"} replace />}
      />
    </Routes>
  );
}
