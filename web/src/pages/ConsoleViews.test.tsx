import { screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it } from "vitest";
import type { MonthRevenue, TenantFleet } from "../api/types";
import { Layout } from "../components/Layout";
import { staffAs } from "../test/fixtures";
import { renderPage } from "../test/render";
import { server } from "../test/server";
import ConsoleFleet from "./ConsoleFleet";
import ConsoleRevenue from "./ConsoleRevenue";

const tenant = (slug: string, name: string) => ({
  id: slug,
  slug,
  name,
  status: "trial" as const,
  partner_id: null,
  created_at: null,
  on_hold: false,
});

const empty = (period: string): MonthRevenue => ({
  period,
  invoices: 0,
  subtotal: null,
  tax: null,
  total: null,
  paid: null,
  outstanding: null,
  direct: null,
  wholesale: null,
  placeholder: false,
});

describe("Cassava's console views", () => {
  beforeEach(() =>
    server.use(
      http.get("/api/v1/sites", () => HttpResponse.json([])),
      http.get("/api/v1/bays", () => HttpResponse.json([])),
    ),
  );

  it("puts tenants needing attention first and says plainly what a node has not reported", async () => {
    const fleet: TenantFleet[] = [
      {
        tenant: tenant("quiet", "Quiet Co"),
        nodes: [
          {
            id: "old",
            name: "Old edge",
            health: "revoked",
            last_seen_at: null,
            version: null,
            cameras_reported: 1,
            cameras_connected: 0,
            spool_pending: null,
          },
        ],
        health: { revoked: 1 },
        needs_attention: false,
      },
      {
        tenant: tenant("bakers-inn", "Bakers Inn"),
        nodes: [
          {
            id: "n1",
            name: "Dock edge",
            health: "online",
            last_seen_at: "2026-10-02T09:00:00Z",
            version: "0.4.0",
            cameras_reported: 2,
            cameras_connected: 1,
            spool_pending: 3,
          },
          {
            id: "n2",
            name: "Spare edge",
            health: "never_seen",
            last_seen_at: null,
            version: null,
            cameras_reported: null,
            cameras_connected: null,
            spool_pending: null,
          },
        ],
        health: { online: 1, never_seen: 1 },
        needs_attention: true,
      },
    ];
    server.use(http.get("/api/v1/platform/fleet", () => HttpResponse.json(fleet)));
    renderPage(<ConsoleFleet />);
    expect(await screen.findByText("1 of 2 edge nodes online, across 1 tenants.")).toBeInTheDocument();
    const regions = screen.getAllByRole("region");
    expect(regions[0]).toHaveAccessibleName("Bakers Inn");
    expect(within(regions[0]).getByText("needs attention")).toBeInTheDocument();
    expect(within(regions[0]).getByText(/1 of 2 cameras streaming · 3 queued/)).toBeInTheDocument();
    expect(within(regions[0]).getByText(/cameras not reported .* never heard from/)).toBeInTheDocument();
    expect(within(regions[1]).getByText("Nothing installed")).toBeInTheDocument();
    expect(within(regions[1]).getByText("1 revoked node not shown")).toBeInTheDocument();
    expect(within(regions[1]).queryByText("Old edge")).not.toBeInTheDocument();
  });

  it("shows revenue issued, and no figure for a month with nothing issued", async () => {
    server.use(
      http.get("/api/v1/platform/revenue", () =>
        HttpResponse.json({
          currency: "USD",
          months: [
            empty("2026-09"),
            {
              period: "2026-10",
              invoices: 2,
              subtotal: "2092.42",
              tax: "313.86",
              total: "2406.28",
              paid: "500.00",
              outstanding: "1906.28",
              direct: "1030.00",
              wholesale: "1062.42",
              placeholder: true,
            },
          ],
          overdue: 2,
          overdue_amount: "1906.28",
          by_payer: { "Direct Co": "1030.00", LITZIM: "1062.42" },
          placeholder: true,
        }),
      ),
    );
    renderPage(<ConsoleRevenue />);
    expect(await screen.findByText("2092.42")).toBeInTheDocument();
    expect(screen.getByText("Nothing issued")).toBeInTheDocument();
    expect(screen.queryByText("0.00")).not.toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("placeholder prices");
    expect(screen.getByRole("alert")).toHaveTextContent("2 invoices are overdue: 1906.28 USD");
  });

  it("offers Revenue to Cassava only, not to a partner", () => {
    const { unmount } = renderPage(
      <Layout connected me={staffAs("platform")} onLogout={() => {}}>
        <div />
      </Layout>,
    );
    const rail = () => within(screen.getAllByRole("navigation")[0]);
    expect(rail().getByRole("link", { name: /Revenue/ })).toBeInTheDocument();
    expect(rail().getByRole("link", { name: /Fleet/ })).toBeInTheDocument();
    unmount();
    renderPage(
      <Layout connected me={staffAs("litzim")} onLogout={() => {}}>
        <div />
      </Layout>,
    );
    expect(rail().queryByRole("link", { name: /Revenue/ })).not.toBeInTheDocument();
    expect(rail().getByRole("link", { name: /Fleet/ })).toBeInTheDocument();
  });
});
