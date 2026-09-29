import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ScopeProvider } from "../api/scope";
import { ToastProvider } from "../components/toast";
import { LiveActivityProvider } from "../live/provider";
import { MotionRoot } from "../motion";

/** Render a page the way App does: query client, router, motion, toasts, live feed. */
export function renderPage(ui: ReactElement, { path = "/", route = "/" } = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <MotionRoot>
          <ToastProvider>
            <ScopeProvider>
              <LiveActivityProvider>
                <Routes>
                  <Route path={route} element={ui} />
                </Routes>
              </LiveActivityProvider>
            </ScopeProvider>
          </ToastProvider>
        </MotionRoot>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
