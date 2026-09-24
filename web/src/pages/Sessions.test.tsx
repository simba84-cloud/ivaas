import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";
import { bay, session, site } from "../test/fixtures";
import { renderPage } from "../test/render";
import { server } from "../test/server";
import Sessions from "./Sessions";

const me = (roles: string[]) => ({ subject: "u", name: "u", roles });

const disputed = session({
  id: "d1",
  status: "disputed",
  plate: "ABC 1234",
  ai_count: 42,
  manual_count: 30,
  variance: 12,
  accuracy: 0.6,
});

const row = (plate: string) => screen.getByText(plate).closest("tr") as HTMLElement;

function api(rows = [disputed]) {
  server.use(
    // the page reads its bay from the shared scope, so the shell's calls are needed too
    http.get("/api/v1/sites", () => HttpResponse.json([site])),
    http.get("/api/v1/bays", () => HttpResponse.json([bay])),
    http.get("/api/v1/sessions", () => HttpResponse.json(rows)),
  );
}

describe("reconciliation sign-off", () => {
  it("lets an admin approve a disputed load with a reason and a note", async () => {
    api();
    const approve = vi.fn();
    server.use(
      http.post("/api/v1/sessions/d1/approve", async ({ request }) => {
        approve(await request.json());
        return HttpResponse.json({
          ...disputed,
          status: "approved",
          approved_by: "u",
          approved_at: "2026-09-24T10:00:00Z",
          approval_reason: "camera_blocked",
          approval_note: "forklift parked in view",
        });
      }),
    );
    renderPage(<Sessions me={me(["admin"])} />, { path: "/sessions", route: "/sessions" });

    await userEvent.click(await screen.findByRole("button", { name: "Approve" }));
    await userEvent.selectOptions(
      screen.getByLabelText("Approval reason"),
      "camera_blocked",
    );
    await userEvent.type(screen.getByLabelText("Approval note"), "forklift parked in view");
    await userEvent.click(screen.getByRole("button", { name: /Confirm|Saving/ }));

    expect(approve).toHaveBeenCalledWith({
      reason: "camera_blocked",
      note: "forklift parked in view",
    });
  });

  it("shows who signed a load off, when and why", async () => {
    api([
      session({
        id: "a1",
        status: "approved",
        plate: "XYZ 9999",
        ai_count: 42,
        manual_count: 30,
        variance: 12,
        accuracy: 0.6,
        approved_by: "site.admin",
        approved_at: "2026-09-24T10:00:00Z",
        approval_reason: "damaged_removed",
        approval_note: "12 crates pulled damaged",
      }),
    ]);
    renderPage(<Sessions me={me(["admin"])} />, { path: "/sessions", route: "/sessions" });

    await screen.findByText("XYZ 9999");
    const r = row("XYZ 9999");
    expect(within(r).getByText("Damaged crates removed")).toBeInTheDocument();
    expect(within(r).getByText(/site\.admin/)).toBeInTheDocument();
    // the discrepancy stays on the record after sign-off
    expect(within(r).getByText("+12")).toBeInTheDocument();
    expect(within(r).getByText("60.0%")).toBeInTheDocument();
  });

  it("does not offer sign-off to an operator, who sees it is pending", async () => {
    api();
    renderPage(<Sessions me={me(["operator"])} />, { path: "/sessions", route: "/sessions" });

    expect(await screen.findByText("Awaiting sign-off")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });

  it("offers no sign-off on a load that is not disputed", async () => {
    api([session({ id: "r1", plate: "OK 0001", status: "reconciled", manual_count: 42 })]);
    renderPage(<Sessions me={me(["admin"])} />, { path: "/sessions", route: "/sessions" });

    await screen.findByText("OK 0001");
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });
});
