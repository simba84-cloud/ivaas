import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ScopeProvider } from "../api/scope";

/** Render a page the way App does: query client + router, at a given path. */
export function renderPage(ui: ReactElement, { path = "/", route = "/" } = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <ScopeProvider>
          <Routes>
            <Route path={route} element={ui} />
          </Routes>
        </ScopeProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
