import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";

import { App } from "./App";
import "./i18n";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});

const root = document.getElementById("root");
if (!root) {
  throw new Error("Root element #root not found");
}

// The service worker keeps the built shell for opening without connection (§ 10.4.3). Only in
// a production build: the development server must always serve fresh files.
if (import.meta.env.PROD && "serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    void navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => undefined);
  });
}

// A click anywhere in a field of a date or a time opens the calendar of the browser, not only
// the small icon at its edge (decision of 15.11.2026).
document.addEventListener("click", (event) => {
  const field = event.target;
  if (!(field instanceof HTMLInputElement)) return;
  if (!["date", "datetime-local", "time", "month"].includes(field.type)) return;
  if (field.disabled || field.readOnly) return;
  try {
    field.showPicker();
  } catch {
    // An old browser or a call out of a gesture: the field stays an ordinary one.
  }
});

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
