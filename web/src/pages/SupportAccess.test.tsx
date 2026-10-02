import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it } from "vitest";
import type { BreakGlassGrant } from "../api/types";
import { BreakGlassBanner, Layout } from "../components/Layout";
import { meAs, staffAs } from "../test/fixtures";
import { renderPage } from "../test/render";
import { server } from "../test/server";
import SupportAccess from "./SupportAccess";
import SupportConsole from "./SupportConsole";

const grant = (over: Partial<BreakGlassGrant> = {}): BreakGlassGrant => ({
  id: "g1",
  tenant_id: "t",
  tenant_name: "Bakers Inn",
  requested_by: "support",
  reason: "Bay counts doubled since Monday, ticket 4411",
  minutes: 60,
  requested_at: "2026-10-01T09:00:00Z",
  state: "pending",
  decided_by: null,
  decided_at: null,
  expires_at: null,
  ended_by: null,
  ended_at: null,
  ...over,
});

describe("break-glass", () => {
  beforeEach(() =>
    server.use(
      http.get("/api/v1/sites", () => HttpResponse.json([])),
      http.get("/api/v1/bays", () => HttpResponse.json([])),
    ),
  );

  it("T8.3: the owner sees what support asks and why, and approves it", async () => {
    let state: BreakGlassGrant = grant();
    let decided = "";
    server.use(
      http.get("/api/v1/support-access", () => HttpResponse.json([state])),
      http.post("/api/v1/support-access/g1/:action", ({ params }) => {
        decided = String(params.action);
        state = grant({ state: "active", decided_by: "owner", decided_at: "2026-10-01T10:00:00Z", expires_at: "2026-10-01T11:00:00Z" });
        return HttpResponse.json(state);
      }),
    );
    const user = userEvent.setup();
    renderPage(<SupportAccess />);
    expect(await screen.findByText(/ticket 4411/)).toBeInTheDocument();
    expect(screen.getByText("pending")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Approve" }));
    expect(decided).toBe("approve");
    expect(await screen.findByRole("button", { name: "End access now" })).toBeInTheDocument();
    expect(screen.getByText(/Approved by owner/)).toBeInTheDocument();
  });

  it("support asks a tenant, and can open it only once the owner has approved", async () => {
    let asked: unknown;
    server.use(
      http.get("/api/v1/platform/tenants", () =>
        HttpResponse.json([{ id: "t", slug: "bakers-inn", name: "Bakers Inn", status: "trial", partner_id: null, created_at: null, on_hold: false }]),
      ),
      http.get("/api/v1/platform/break-glass", () =>
        HttpResponse.json([grant(), grant({ id: "g0", state: "active", expires_at: "2026-10-01T11:00:00Z" })]),
      ),
      http.post("/api/v1/platform/tenants/t/break-glass", async ({ request }) => {
        asked = await request.json();
        return HttpResponse.json(grant(), { status: 201 });
      }),
    );
    const user = userEvent.setup();
    renderPage(<SupportConsole />);
    await screen.findByRole("option", { name: "Bakers Inn" });
    await user.type(screen.getByLabelText("Why"), "Bay counts doubled since Monday");
    await user.click(screen.getByRole("button", { name: "Ask the owner" }));
    expect(asked).toEqual({ reason: "Bay counts doubled since Monday", minutes: 60 });
    const rows = await screen.findAllByRole("row");
    // the pending one cannot be opened; the approved one can
    expect(within(rows[1]).queryByRole("button", { name: /Open/ })).not.toBeInTheDocument();
    expect(within(rows[1]).getByRole("button", { name: "Withdraw" })).toBeInTheDocument();
    expect(within(rows[2]).getByRole("button", { name: "Open Bakers Inn" })).toBeInTheDocument();
  });

  it("says, on every page, whose data support is in and that it is recorded", () => {
    const me = {
      ...meAs(["viewer"]),
      roles: ["break_glass"],
      break_glass: { grant_id: "g0", expires_at: "2026-10-01T11:00:00Z" },
    };
    renderPage(<BreakGlassBanner me={me} />);
    expect(screen.getByRole("alert")).toHaveTextContent(/Support access to Bakers Inn, read-only, until .*recorded in its audit log/);
    expect(screen.getByRole("button", { name: "End access" })).toBeInTheDocument();
  });

  it("offers the owner Support Access, and support only its own console", () => {
    const { unmount } = renderPage(
      <Layout connected me={meAs(["owner"])} onLogout={() => {}}>
        <div />
      </Layout>,
    );
    expect(within(screen.getAllByRole("navigation")[0]).getByRole("link", { name: /Support Access/ })).toHaveAttribute(
      "href",
      "/support-access",
    );
    unmount();
    renderPage(
      <Layout connected me={staffAs("support")} onLogout={() => {}}>
        <div />
      </Layout>,
    );
    const rail = within(screen.getAllByRole("navigation")[0]);
    expect(rail.getByRole("link", { name: /Support Access/ })).toHaveAttribute("href", "/console/support");
    expect(rail.queryByRole("link", { name: /Tenants/ })).not.toBeInTheDocument();
  });
});
