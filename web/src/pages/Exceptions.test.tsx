import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";
import { bay, meAs, site } from "../test/fixtures";
import { renderPage } from "../test/render";
import { server } from "../test/server";
import Balances from "./Balances";
import Exceptions from "./Exceptions";

const scope = () =>
  server.use(
    http.get("/api/v1/sites", () => HttpResponse.json([site])),
    http.get("/api/v1/bays", () => HttpResponse.json([bay])),
  );

const short = {
  id: "x1",
  kind: "count_mismatch",
  day: "2026-10-01",
  plate: "ABC 1234",
  route: "Route 7",
  expected: 1200,
  counted: 1150,
  difference: -50,
  session_id: "s1",
  status: "open",
  raised_at: "2026-10-01T10:00:00Z",
  resolved_by: null,
  resolved_at: null,
  resolution_note: null,
};

describe("exceptions", () => {
  it("shows the difference, opens the load's evidence, and resolves with a note", async () => {
    scope();
    const resolved = vi.fn();
    server.use(
      http.get("/api/v1/exceptions", () => HttpResponse.json([short])),
      http.get("/api/v1/sessions/s1/evidence", () => HttpResponse.json([])),
      http.post("/api/v1/exceptions/x1/resolve", async ({ request }) => {
        resolved(await request.json());
        return HttpResponse.json({ ...short, status: "resolved" });
      }),
    );
    renderPage(<Exceptions me={meAs(["admin"])} />, { path: "/exceptions", route: "/exceptions" });
    expect(await screen.findByText("Count differs from manifest")).toBeInTheDocument();
    expect(screen.getByText(/manifest 1,200 · counted 1,150/)).toBeInTheDocument();
    expect(screen.getByText("(-50)")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Evidence" }));
    expect(await screen.findByText(/No evidence clips for this load/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("What was found"), "20 left on the dock");
    await userEvent.click(screen.getByRole("button", { name: "Resolve" }));
    expect(resolved).toHaveBeenCalledWith({ note: "20 left on the dock" });
  });

  it("says why there is nothing, and hides actions from those who cannot take them", async () => {
    scope();
    server.use(http.get("/api/v1/exceptions", () => HttpResponse.json([])));
    renderPage(<Exceptions me={meAs(["viewer"])} />, { path: "/exceptions", route: "/exceptions" });
    expect(await screen.findByText(/Without manifests there is nothing to compare against/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Import manifest/ })).not.toBeInTheDocument();
  });
});

describe("balances", () => {
  it("lists what is outstanding per truck with totals, never counting open loads", async () => {
    scope();
    server.use(
      http.get("/api/v1/balances", () =>
        HttpResponse.json([
          { key: "ABC 1003", dispatched: 200, returned: 150, outstanding: 50, loads_out: 1, loads_back: 1, in_progress: 0, corrected: 0 },
          { key: "ABC 1001", dispatched: 120, returned: 120, outstanding: 0, loads_out: 1, loads_back: 2, in_progress: 1, corrected: 1 },
        ]),
      ),
    );
    renderPage(<Balances />, { path: "/balances", route: "/balances" });
    expect(await screen.findByText("ABC 1003")).toBeInTheDocument();
    expect(screen.getByText("1 still at the bay · 1 corrected by a person")).toBeInTheDocument();
    expect(screen.getByText("320")).toBeInTheDocument(); // dispatched total
    expect(screen.getByText("1 load(s) still being counted")).toBeInTheDocument();
  });
});
