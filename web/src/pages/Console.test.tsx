import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it } from "vitest";
import type { Onboarding, OnboardingStep, TenantRecord } from "../api/types";
import { Layout } from "../components/Layout";
import { meAs, staffAs } from "../test/fixtures";
import { renderPage } from "../test/render";
import { server } from "../test/server";
import Console from "./Console";
import ConsoleTenant from "./ConsoleTenant";
import Onboard from "./Onboard";
import PartnerInvoices from "./PartnerInvoices";

const LITZIM = { id: "p1", slug: "litzim", name: "LITZIM" };
const tenant = (over: Partial<TenantRecord> = {}): TenantRecord => ({
  id: "t9",
  slug: "chipo-foods",
  name: "Chipo Foods",
  status: "trial",
  partner_id: "p1",
  created_at: "2026-10-01T09:00:00Z",
  on_hold: false,
  ...over,
});
const NAMES: OnboardingStep["name"][] = [
  "tenant_created",
  "owner_signed_in",
  "plan_set",
  "site_and_bay",
  "enrollment_token",
  "node_enrolled",
  "node_reporting",
];
const progress = (done: number, over: Partial<Onboarding> = {}): Onboarding => ({
  tenant: tenant(),
  steps: NAMES.map((name, i) => ({ name, done: i < done, at: null, detail: "" })),
  sites: [],
  bays: [],
  nodes: [],
  seconds_to_first_node: null,
  target_seconds: 1800,
  within_target: null,
  ...over,
});
const book = {
  version: "2026-10-placeholder",
  placeholder: true,
  currency: "USD",
  plans: [{ id: "standard", name: "Standard", recurring: {}, term_days: null }],
};

describe("consoles", () => {
  beforeEach(() =>
    server.use(
      http.get("/api/v1/platform/partners", () => HttpResponse.json([LITZIM])),
      http.get("/api/v1/billing/price-book", () => HttpResponse.json(book)),
      // the test harness's scope provider asks; the app's does not, for staff
      http.get("/api/v1/sites", () => HttpResponse.json([])),
      http.get("/api/v1/bays", () => HttpResponse.json([])),
    ),
  );

  it("gives staff the consoles instead of a tenant's pages", async () => {
    renderPage(
      <Layout connected me={staffAs("litzim")} onLogout={() => {}}>
        <div />
      </Layout>,
    );
    const rail = screen.getAllByRole("navigation")[0];
    expect(within(rail).getByRole("link", { name: /Tenants/ })).toBeInTheDocument();
    expect(within(rail).getByRole("link", { name: /Onboard a Tenant/ })).toBeInTheDocument();
    expect(within(rail).queryByRole("link", { name: /Dashboard/ })).not.toBeInTheDocument();
    expect(screen.getByText("Partner console")).toBeInTheDocument();
    expect(screen.queryByText("Reconnecting")).not.toBeInTheDocument();
  });

  it("keeps a tenant's people on their own pages", () => {
    renderPage(
      <Layout connected me={meAs(["owner"])} onLogout={() => {}}>
        <div />
      </Layout>,
    );
    const rail = screen.getAllByRole("navigation")[0];
    expect(within(rail).queryByRole("link", { name: /Onboard a Tenant/ })).not.toBeInTheDocument();
  });

  it("lists the tenants with how each is billed and where it stands", async () => {
    server.use(
      http.get("/api/v1/platform/tenants", () =>
        HttpResponse.json([tenant(), tenant({ id: "t2", name: "Direct Co", partner_id: null, status: "suspended", on_hold: false })]),
      ),
    );
    renderPage(<Console me={staffAs("platform")} />, { path: "/console", route: "/console" });
    expect(await screen.findByRole("link", { name: "Chipo Foods" })).toHaveAttribute("href", "/console/tenants/t9");
    expect(await screen.findByText("LITZIM")).toBeInTheDocument();
    expect(screen.getByText("Cassava directly")).toBeInTheDocument();
    expect(screen.getByText("suspended")).toBeInTheDocument();
  });

  it("T8.1: onboards a tenant, shows the owner's password once, then sets up the site and a token", async () => {
    let posted: { body: Record<string, unknown>; key: string | null } | undefined;
    let state = progress(1);
    server.use(
      http.post("/api/v1/platform/tenants", async ({ request }) => {
        posted = { body: (await request.json()) as Record<string, unknown>, key: request.headers.get("Idempotency-Key") };
        return HttpResponse.json({ tenant: tenant(), owner_username: "chipo-foods.owner", temporary_password: "Brisk-Otter-42", created: true, steps: [] });
      }),
      http.get("/api/v1/platform/tenants/t9/onboarding", () => HttpResponse.json(state)),
      http.post("/api/v1/platform/tenants/t9/onboarding/site", async ({ request }) => {
        const body = (await request.json()) as { site_name: string; bay_name: string };
        const site = { id: "s1", name: body.site_name, timezone: "Africa/Harare" };
        const bay = { id: "b1", site_id: "s1", name: body.bay_name, height_m: 4, width_m: 3 };
        state = progress(4, { sites: [site], bays: [bay] });
        return HttpResponse.json({ site, bay }, { status: 201 });
      }),
      http.post("/api/v1/platform/tenants/t9/onboarding/enrollment-tokens", () =>
        HttpResponse.json(
          { token: "ivaas-enr-abc.def", name: "Edge 1", site_id: "s1", bay_id: "b1", expires_at: "2026-10-02T09:00:00Z" },
          { status: 201 },
        ),
      ),
    );
    const user = userEvent.setup();
    renderPage(<Onboard me={staffAs("litzim")} />, { path: "/console/onboard", route: "/console/onboard" });

    await user.type(screen.getByLabelText("Customer name"), "Chipo Foods");
    expect(screen.getByLabelText("Short name")).toHaveValue("chipo-foods");
    // a partner's customers are its own: it is not asked whom to bill through
    expect(screen.queryByLabelText("Billed through")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Create tenant" }));

    expect(await screen.findByText("Brisk-Otter-42")).toBeInTheDocument();
    expect(posted!.body).toMatchObject({ slug: "chipo-foods", name: "Chipo Foods", owner_username: "chipo-foods.owner" });
    expect(posted!.key).toMatch(/^console-/);

    await user.type(await screen.findByLabelText("Site"), "Msasa Bakery");
    await user.click(screen.getByRole("button", { name: "Create site" }));
    await user.click(await screen.findByRole("button", { name: "Make enrollment token" }));
    expect(await screen.findByText(/ivaas_pipeline enroll .* --token ivaas-enr-abc\.def/)).toBeInTheDocument();
  });

  it("says how long the first node took against the half hour, and invents no time before one", async () => {
    server.use(
      http.get("/api/v1/platform/tenants/t9", () => HttpResponse.json(tenant())),
      http.get("/api/v1/platform/tenants/t9/subscription", () =>
        HttpResponse.json({ subscribed: false, entitlements: null, segments: [], channels_in_use: { od: 0, lpr: 0 }, usage_this_month: {} }),
      ),
      http.get("/api/v1/platform/tenants/t9/onboarding", () => HttpResponse.json(progress(2))),
    );
    const { unmount } = renderPage(<ConsoleTenant me={staffAs("litzim")} />, {
      path: "/console/tenants/t9",
      route: "/console/tenants/:id",
    });
    expect(await screen.findByText(/No node enrolled yet/)).toBeInTheDocument();
    expect(screen.queryByText(/0 min after creation/)).not.toBeInTheDocument();
    // LITZIM's customer is LITZIM's to invoice: no Cassava invoice panel for it
    expect(screen.queryByRole("region", { name: "Invoices" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Hold this customer" })).toBeInTheDocument();
    unmount();

    const node = {
      id: "n1",
      name: "Msasa edge",
      site_id: "s1",
      bay_id: "b1",
      health: "online",
      enrolled_at: "2026-10-01T09:12:00Z",
      last_seen_at: "2026-10-01T09:13:00Z",
      version: "0.4.0",
    };
    server.use(
      http.get("/api/v1/platform/tenants/t9/onboarding", () =>
        HttpResponse.json(progress(7, { nodes: [node], seconds_to_first_node: 720, within_target: true })),
      ),
    );
    renderPage(<ConsoleTenant me={staffAs("litzim")} />, { path: "/console/tenants/t9", route: "/console/tenants/:id" });
    expect(await screen.findByText(/First node enrolled 12 min after creation, inside the 30 min target/)).toBeInTheDocument();
    expect(screen.getByText("online")).toBeInTheDocument();
  });

  it("lets Cassava issue a partner's invoice and a partner only read its own", async () => {
    const draft = {
      partner: "LITZIM",
      number: null,
      period_start: "2026-10-01",
      period_end: "2026-10-31",
      currency: "USD",
      customers: [{ tenant_id: "t9", tenant_name: "Chipo Foods", lines: [], subtotal: "721.00" }],
      subtotal: "721.00",
      tax_name: "VAT",
      tax: "108.15",
      total: "829.15",
      price_book: "2026-10-placeholder+wholesale-litzim",
      placeholder: true,
      stamp: "PLACEHOLDER PRICES: NOT FOR ISSUE",
      issued_at: null,
      due_date: null,
      paid: "0.00",
      settled: false,
    };
    server.use(
      http.get("/api/v1/platform/partners/p1/invoices/draft", () => HttpResponse.json(draft)),
      http.get("/api/v1/platform/partners/p1/commission", () =>
        HttpResponse.json({
          partner: "LITZIM",
          period_start: "2026-10-01",
          period_end: "2026-10-31",
          currency: "USD",
          discount: "0.30",
          lines: [{ tenant_id: "t", tenant_name: "Bakers Inn", at_list: "487.74", at_wholesale: "341.42", margin: "146.32" }],
          at_list: "1517.74",
          at_wholesale: "1062.42",
          margin: "455.32",
          tax_note: "before tax",
          wholesale_invoice: null,
          price_book: "2026-10-placeholder+wholesale-litzim",
          placeholder: true,
          stamp: "PLACEHOLDER PRICES: NOT FOR ISSUE",
        }),
      ),
      http.get("/api/v1/platform/partners/p1/invoices", () =>
        HttpResponse.json([{ ...draft, number: "IVAAS-2026-000003", due_date: "2026-11-16" }]),
      ),
    );
    const { unmount } = renderPage(<PartnerInvoices me={staffAs("platform")} />);
    expect(await screen.findByText("Chipo Foods")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Issue/ })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Record payment" })).toBeInTheDocument();
    unmount();

    renderPage(<PartnerInvoices me={staffAs("litzim")} />);
    expect(await screen.findByText("IVAAS-2026-000003")).toBeInTheDocument();
    // the commission statement: margin at list, said to be no more than that
    const statement = await screen.findByLabelText("LITZIM commission");
    expect(within(statement).getByText("146.32")).toBeInTheDocument();
    expect(within(statement).getByText(/455.32 USD/)).toBeInTheDocument();
    expect(within(statement).getByText(/30% wholesale discount, before tax/)).toBeInTheDocument();
    expect(within(statement).getByText(/not issued yet/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Issue/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Record payment" })).not.toBeInTheDocument();
  });
});
