import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import type { CrateTransaction, DriverBalance } from "../api/types";
import { bay, meAs, site } from "../test/fixtures";
import { renderPage } from "../test/render";
import { server } from "../test/server";
import BayKiosk from "./BayKiosk";
import CrateControl from "./CrateControl";
import Drivers from "./Drivers";

const scope = () =>
  server.use(
    http.get("/api/v1/sites", () => HttpResponse.json([site])),
    http.get("/api/v1/bays", () => HttpResponse.json([bay])),
    http.get("/api/v1/bay-standards", () =>
      HttpResponse.json([{ bay_id: bay.id, columns: 2, layers: 14, crates_per_unit: 28, controlled: true }]),
    ),
  );

const txn = (over: Partial<CrateTransaction> = {}): CrateTransaction => ({
  id: "t1",
  bay_id: bay.id,
  bay_name: bay.name,
  driver_id: "d1",
  driver_name: "Tendai Moyo",
  badge_code: "BI0042",
  session_id: "s1",
  plate: "ABC 1234",
  direction: "offloading",
  declared_units: 1,
  standard_columns: 2,
  standard_layers: 14,
  expected_crates: 28,
  status: "open",
  validated_status: null,
  opened_at: "2026-11-11T08:00:00Z",
  closed_at: null,
  checked_in_by: "kiosk",
  full_columns: null,
  non_standard_columns: null,
  unsure_columns: null,
  counted_crates: null,
  accepted_by: null,
  accepted_at: null,
  accept_reason: null,
  accept_note: null,
  progress: null,
  ...over,
});

describe("driver register", () => {
  it("lists drivers without ever showing a PIN", async () => {
    scope();
    server.use(
      http.get("/api/v1/drivers", () =>
        HttpResponse.json([
          { id: "d1", name: "Tendai Moyo", badge_code: "BI0042", employee_ref: "E1", phone: "", active: true, has_pin: true, created_at: null },
        ]),
      ),
    );
    renderPage(<Drivers me={meAs(["admin"])} />, { path: "/drivers", route: "/drivers" });
    // once in the register, once on the print-only badge sheet
    expect(await screen.findAllByText("Tendai Moyo")).toHaveLength(2);
    expect(screen.getByRole("button", { name: /PIN for Tendai Moyo/ })).toHaveTextContent("Change PIN");
    expect(screen.getByRole("button", { name: /Add driver/ })).toBeInTheDocument();
  });

  it("offers no editing without the right", async () => {
    scope();
    server.use(http.get("/api/v1/drivers", () => HttpResponse.json([])));
    renderPage(<Drivers me={meAs(["operator"])} />, { path: "/drivers", route: "/drivers" });
    expect(await screen.findByText(/No drivers registered/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Add driver/ })).not.toBeInTheDocument();
  });
});

describe("bay kiosk", () => {
  it("checks a driver in with what they declared", async () => {
    scope();
    let sent: unknown = null;
    server.use(
      http.get(`/api/v1/bays/${bay.id}/transaction`, () => HttpResponse.json(null)),
      http.post(`/api/v1/bays/${bay.id}/check-in`, async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json(txn({ direction: "loading" }), { status: 201 });
      }),
    );
    renderPage(<BayKiosk />, { path: `/bay/${bay.id}`, route: "/bay/:bayId" });
    const badge = await screen.findByLabelText(/Scan or type your badge/);
    await userEvent.type(badge, "BI0042");
    await userEvent.click(screen.getByRole("button", { name: "One more unit" }));
    expect(screen.getByText(/56 crates/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Start" }));
    expect(sent).toEqual({ badge: "BI0042", direction: "loading", declared_units: 2 });
  });

  it("shows unmeasured columns as unmeasured while counting", async () => {
    scope();
    server.use(
      http.get(`/api/v1/bays/${bay.id}/transaction`, () =>
        HttpResponse.json(
          txn({
            progress: {
              status: "unverified",
              expected_columns: 2,
              expected_crates: 28,
              full_columns: 1,
              non_standard_columns: 0,
              unsure_columns: 1,
              counted_crates: null,
              columns: [
                { crates: 14, verdict: "full" },
                { crates: 14, verdict: "unsure" },
              ],
            },
          }),
        ),
      ),
    );
    renderPage(<BayKiosk />, { path: `/bay/${bay.id}`, route: "/bay/:bayId" });
    expect(await screen.findByText("Tendai Moyo")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("1 of 2 columns at the standard height");
    expect(screen.getByRole("status")).toHaveTextContent("1 could not be measured");
  });
});

describe("crate control", () => {
  it("keeps unmeasured transactions beside the balance, not in it", async () => {
    scope();
    const b: DriverBalance = {
      driver_id: "d1",
      driver_name: "Tendai Moyo",
      badge_code: "BI0042",
      issued: 56,
      returned: 42,
      outstanding: 14,
      dispatches: 1,
      returns: 1,
      in_progress: 0,
      exceptions: 2,
      unmeasured: 1,
      unmeasured_declared_out: 0,
      unmeasured_declared_back: 28,
      last_at: null,
    };
    server.use(http.get("/api/v1/drivers/balances", () => HttpResponse.json([b])));
    renderPage(<CrateControl me={meAs(["admin"])} />, { path: "/crates", route: "/crates" });
    expect(await screen.findByText("Tendai Moyo")).toBeInTheDocument();
    expect(screen.getByText("14")).toBeInTheDocument();
    expect(screen.getByText(/28 declared back/)).toBeInTheDocument();
  });

  it("lets a supervisor accept a short return", async () => {
    scope();
    let accepted: unknown = null;
    server.use(
      http.get("/api/v1/drivers/balances", () => HttpResponse.json([])),
      http.get("/api/v1/crate-transactions", () =>
        HttpResponse.json([txn({ status: "short", validated_status: "short", counted_crates: 26 })]),
      ),
      http.post("/api/v1/crate-transactions/t1/accept", async ({ request }) => {
        accepted = await request.json();
        return HttpResponse.json(txn({ status: "accepted", validated_status: "short", counted_crates: 26 }));
      }),
    );
    renderPage(<CrateControl me={meAs(["admin"])} />, { path: "/crates", route: "/crates" });
    await userEvent.click(await screen.findByRole("button", { name: "Transactions" }));
    expect(await screen.findByText("Short")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Accept…/ }));
    await userEvent.click(screen.getByRole("button", { name: "Accept" }));
    expect(accepted).toEqual({ reason: "damaged_crates", note: null });
  });

  it("shows an operator the exception but not the means to accept it", async () => {
    scope();
    server.use(
      http.get("/api/v1/drivers/balances", () => HttpResponse.json([])),
      http.get("/api/v1/crate-transactions", () =>
        HttpResponse.json([txn({ status: "short", validated_status: "short", counted_crates: 26 })]),
      ),
    );
    renderPage(<CrateControl me={meAs(["operator"])} />, { path: "/crates", route: "/crates" });
    await userEvent.click(await screen.findByRole("button", { name: "Transactions" }));
    expect(await screen.findByText("Short")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Accept/ })).not.toBeInTheDocument();
  });
});
