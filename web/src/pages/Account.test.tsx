import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it } from "vitest";
import type { CertificateView, LifecycleView } from "../api/types";
import { Layout } from "../components/Layout";
import { meAs, staffAs } from "../test/fixtures";
import { renderPage } from "../test/render";
import { server } from "../test/server";
import Account from "./Account";
import Certificates from "./Certificates";
import ConsoleTenant from "./ConsoleTenant";

const life = (over: Partial<LifecycleView> = {}): LifecycleView => ({
  tenant_id: "t",
  slug: "bakers-inn",
  name: "Bakers Inn",
  status: "trial",
  cancelled_at: null,
  cancelled_by: null,
  purge_after: null,
  retention_days: 90,
  export_available: true,
  ...over,
});

describe("the end of a tenant", () => {
  beforeEach(() =>
    server.use(
      http.get("/api/v1/sites", () => HttpResponse.json([])),
      http.get("/api/v1/bays", () => HttpResponse.json([])),
    ),
  );

  it("lets the owner export, and cancel only once the short name is typed", async () => {
    let cancelled: unknown;
    server.use(
      http.get("/api/v1/account/lifecycle", () => HttpResponse.json(life())),
      http.post("/api/v1/account/cancel", async ({ request }) => {
        cancelled = await request.json();
        return HttpResponse.json(life({ status: "cancelled" }));
      }),
    );
    const assign = window.location.assign;
    Object.defineProperty(window, "location", { value: { ...window.location, assign: () => {} }, writable: true });
    const user = userEvent.setup();
    renderPage(<Account me={meAs(["owner"])} />);
    expect(await screen.findByRole("button", { name: /Download the export/ })).toBeEnabled();
    expect(screen.getByText(/never included; the manifest lists what was held back/)).toBeInTheDocument();
    const go = screen.getByRole("button", { name: "Cancel the account" });
    await user.type(screen.getByLabelText("Type bakers-inn to confirm"), "bakers");
    expect(go).toBeDisabled();
    await user.type(screen.getByLabelText("Type bakers-inn to confirm"), "-inn");
    await user.click(go);
    expect(cancelled).toEqual({ confirm: "bakers-inn", reason: "" });
    window.location.assign = assign;
  });

  it("tells a cancelled account when its data goes, and keeps only the export", async () => {
    server.use(
      http.get("/api/v1/account/lifecycle", () =>
        HttpResponse.json(
          life({ status: "cancelled", cancelled_at: "2026-10-01T09:00:00Z", cancelled_by: "owner", purge_after: "2026-12-30T09:00:00Z" }),
        ),
      ),
    );
    renderPage(<Account me={meAs(["owner"])} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/cancelled .* by owner\. Its data is kept until .*Export it/);
    expect(screen.queryByRole("button", { name: "Cancel the account" })).not.toBeInTheDocument();

    const cancelledMe = { ...meAs(["admin"]), tenant: { id: "t", slug: "bakers-inn", name: "Bakers Inn", status: "cancelled" } };
    renderPage(
      <Layout connected me={cancelledMe} onLogout={() => {}}>
        <div />
      </Layout>,
    );
    const rail = within(screen.getAllByRole("navigation").at(-2)!);
    expect(rail.queryByRole("link", { name: /Dashboard/ })).not.toBeInTheDocument();
  });

  it("says the store cannot export rather than offering a broken button", async () => {
    server.use(http.get("/api/v1/account/lifecycle", () => HttpResponse.json(life({ export_available: false }))));
    renderPage(<Account me={meAs(["owner"])} />);
    expect(await screen.findByText(/cannot export/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Download/ })).not.toBeInTheDocument();
  });

  it("purges for Cassava only once cancelled, due, and confirmed", async () => {
    let purged = false;
    server.use(
      http.get("/api/v1/platform/partners", () => HttpResponse.json([])),
      http.get("/api/v1/platform/tenants/t9", () =>
        HttpResponse.json({ id: "t9", slug: "gone-co", name: "Gone Co", status: "cancelled", partner_id: null, created_at: null, on_hold: false }),
      ),
      http.get("/api/v1/platform/tenants/t9/onboarding", () => HttpResponse.json({ error: "x" }, { status: 404 })),
      http.get("/api/v1/platform/tenants/t9/subscription", () =>
        HttpResponse.json({ subscribed: false, entitlements: null, segments: [], channels_in_use: { od: 0, lpr: 0 }, usage_this_month: {} }),
      ),
      http.get("/api/v1/platform/tenants/t9/invoices", () => HttpResponse.json([])),
      http.get("/api/v1/platform/tenants/t9/invoices/draft", () => HttpResponse.json({ detail: "none" }, { status: 404 })),
      http.get("/api/v1/billing/price-book", () => HttpResponse.json({ version: "x", placeholder: true, currency: "USD", plans: [] })),
      http.get("/api/v1/platform/tenants/t9/lifecycle", () =>
        HttpResponse.json(
          life({ tenant_id: "t9", slug: "gone-co", name: "Gone Co", status: "cancelled", cancelled_at: "2026-06-01T00:00:00Z", purge_after: "2026-08-30T00:00:00Z" }),
        ),
      ),
      http.post("/api/v1/platform/tenants/t9/purge", () => {
        purged = true;
        return HttpResponse.json(certificate());
      }),
    );
    const user = userEvent.setup();
    renderPage(<ConsoleTenant me={staffAs("platform")} />, { path: "/console/tenants/t9", route: "/console/tenants/:id" });
    const purge = await screen.findByRole("button", { name: "Purge for good" });
    expect(purge).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reinstate" })).toBeInTheDocument();
    await user.type(screen.getByLabelText("Type gone-co to confirm"), "gone-co");
    await user.click(purge);
    expect(purged).toBe(true);
    expect(await screen.findByText(/0 rows and 0 objects left after/)).toBeInTheDocument();
  });

  it("lists certificates and flags one whose signature no longer matches", async () => {
    server.use(
      http.get("/api/v1/platform/deletion-certificates", () =>
        HttpResponse.json([certificate(), certificate({ id: "c2", tenant_name: "Edited Ltd", valid: false })]),
      ),
    );
    renderPage(<Certificates />);
    expect(await screen.findByText("Gone Co")).toBeInTheDocument();
    expect(screen.getByText("valid")).toBeInTheDocument();
    expect(screen.getByText("does not match")).toBeInTheDocument();
    expect(screen.getAllByText("0 rows, 0 objects")).toHaveLength(2);
  });
});

function certificate(over: Partial<CertificateView> = {}): CertificateView {
  return {
    id: "c1",
    purged_tenant_id: "t9",
    tenant_slug: "gone-co",
    tenant_name: "Gone Co",
    purged_at: "2026-09-01T00:00:00Z",
    purged_by: "platform",
    body: {
      rows_deleted: { sites: 1, sessions: 40 },
      objects_deleted: 3,
      retention_days: 90,
      cancelled_at: "2026-06-01T00:00:00Z",
      scan: { tables_checked: 34, rows_remaining: 0, objects_remaining: 0, object_prefix: "tenants/t9/" },
    },
    signature: "ab",
    valid: true,
    ...over,
  };
}
