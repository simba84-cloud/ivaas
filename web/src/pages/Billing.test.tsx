import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { BillingBanner } from "../components/Layout";
import { bay, meAs, site } from "../test/fixtures";
import { renderPage } from "../test/render";
import { server } from "../test/server";
import Billing from "./Billing";

const subscribed = {
  subscribed: true,
  entitlements: {
    plan: "standard",
    valid_until: null,
    limits: { od_channels: 16, lpr_channels: 1 },
    features: {},
    allowances: { storage_gb_month: 100, assistant_tokens: 200000 },
  },
  segments: [],
  channels_in_use: { od: 16, lpr: 1 },
  usage_this_month: { storage_gb_month: "120", assistant_tokens: "1250" },
};
const book = { version: "2026-10-placeholder", placeholder: true, currency: "USD", plans: [] };
const invoice = (over = {}) => ({
  number: "IVAAS-2026-000001",
  period_start: "2026-10-01",
  period_end: "2026-10-31",
  currency: "USD",
  lines: [{ sku: "ivaas-platform", description: "Standard: ivaas-platform x 1", quantity: "1", unit_price: "250.00", amount: "250.00" }],
  subtotal: "1032.00",
  tax_name: "VAT",
  tax: "154.80",
  total: "1186.80",
  stamp: "PLACEHOLDER PRICES: NOT FOR ISSUE",
  due_date: "2026-01-16",
  paid: "0.00",
  settled: false,
  ...over,
});

const show = () => renderPage(<Billing me={meAs(["owner"])} />, { path: "/billing", route: "/billing" });

describe("billing", () => {
  beforeEach(() =>
    server.use(
      http.get("/api/v1/sites", () => HttpResponse.json([site])),
      http.get("/api/v1/bays", () => HttpResponse.json([bay])),
      http.get("/api/v1/billing/price-book", () => HttpResponse.json(book)),
    ),
  );

  it("shows the plan's limits, this month's draft, and an overdue invoice, all as placeholders", async () => {
    server.use(
      http.get("/api/v1/billing/subscription", () => HttpResponse.json(subscribed)),
      http.get("/api/v1/billing/invoices/draft", () => HttpResponse.json(invoice({ number: null }))),
      http.get("/api/v1/billing/invoices", () => HttpResponse.json([invoice()])),
    );
    show();
    expect(await screen.findByText(/prices are placeholders/)).toBeInTheDocument();
    expect(screen.getByText("Plan: standard")).toBeInTheDocument();
    expect(screen.getByText(/120 of 100 GB-month/)).toBeInTheDocument();
    expect(await screen.findByText("1186.80 USD", { selector: "dd" })).toBeInTheDocument();
    expect(screen.getAllByText("PLACEHOLDER PRICES: NOT FOR ISSUE").length).toBeGreaterThan(0);
    expect(await screen.findByText(/Overdue 2026-01-16/)).toBeInTheDocument();
  });

  it("a tenant its partner bills sees its usage without Cassava's prices", async () => {
    server.use(
      http.get("/api/v1/billing/subscription", () => HttpResponse.json(subscribed)),
      http.get("/api/v1/billing/invoices/draft", () =>
        HttpResponse.json({ detail: "your partner invoices you; see /api/v1/billing/statement for your usage" }, { status: 409 }),
      ),
      http.get("/api/v1/billing/statement", () =>
        HttpResponse.json({
          period_start: "2026-10-01",
          period_end: "2026-10-31",
          billed_by: "LITZIM",
          lines: [{ sku: "ivaas-od-count", description: "POC / Trial: ivaas-od-count x 16", quantity: "16" }],
          usage: {},
        }),
      ),
      http.get("/api/v1/billing/invoices", () => HttpResponse.json([])),
    );
    show();
    expect(await screen.findByText(/LITZIM invoices you for these/)).toBeInTheDocument();
    expect(screen.getByText("x 16")).toBeInTheDocument();
    expect(screen.queryByText(/USD/)).not.toBeInTheDocument();
  });

  it("says when there is no plan, rather than showing zeroes", async () => {
    server.use(
      http.get("/api/v1/billing/subscription", () =>
        HttpResponse.json({ ...subscribed, subscribed: false, entitlements: null }),
      ),
      http.get("/api/v1/billing/invoices", () => HttpResponse.json([])),
    );
    show();
    expect(await screen.findByText("No plan")).toBeInTheDocument();
  });

  it("lets the owner change the plan from now, and only the owner", async () => {
    let sent: unknown = null;
    server.use(
      http.get("/api/v1/billing/subscription", () => HttpResponse.json(subscribed)),
      http.get("/api/v1/billing/invoices/draft", () => HttpResponse.json(invoice({ number: null }))),
      http.get("/api/v1/billing/invoices", () => HttpResponse.json([])),
      http.put("/api/v1/billing/subscription", async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json(subscribed);
      }),
    );
    show();
    const od = await screen.findByLabelText("Counting channels");
    await userEvent.clear(od);
    await userEvent.type(od, "24");
    await userEvent.click(screen.getByRole("button", { name: "Change from now" }));
    await vi.waitFor(() =>
      expect(sent).toEqual({ plan: "standard", quantities: { "ivaas-od-count": 24, "ivaas-lpr": 1 } }),
    );
  });

  it("an auditor sees the plan but not the means to change it", async () => {
    server.use(
      http.get("/api/v1/billing/subscription", () => HttpResponse.json(subscribed)),
      http.get("/api/v1/billing/invoices/draft", () => HttpResponse.json(invoice({ number: null }))),
      http.get("/api/v1/billing/invoices", () => HttpResponse.json([])),
    );
    renderPage(<Billing me={meAs(["viewer"])} />, { path: "/billing", route: "/billing" });
    expect(await screen.findByText("Plan: standard")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Change from now" })).not.toBeInTheDocument();
  });

  it("tells a suspended or late tenant why its changes fail", () => {
    const { rerender } = render(<BillingBanner status="suspended" />);
    expect(screen.getByRole("alert")).toHaveTextContent(/suspended.*counting carries on/);
    rerender(<BillingBanner status="past_due" />);
    expect(screen.getByRole("status")).toHaveTextContent(/overdue/);
    rerender(<BillingBanner status="active" />);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
