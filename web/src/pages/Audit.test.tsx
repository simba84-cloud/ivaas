import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";
import { renderPage } from "../test/render";
import { server } from "../test/server";
import Audit from "./Audit";

const entry = (over = {}) => ({
  id: "a1",
  at: "2026-09-25T10:31:00Z",
  actor: "operator",
  action: "session_reconciled",
  subject: "ABC 1234",
  detail: { ai_count: 42, manual_count: 40, variance: 2, outcome: "disputed" },
  ...over,
});

describe("audit log", () => {
  it("reads as a sentence: who, what, which load, and the numbers", async () => {
    server.use(http.get("/api/v1/audit", () => HttpResponse.json([entry()])));
    renderPage(<Audit />, { path: "/audit", route: "/audit" });

    const row = (await screen.findByText("ABC 1234")).closest("li") as HTMLElement;
    expect(within(row).getByText("operator")).toBeInTheDocument();
    expect(within(row).getByText("Verified a count")).toBeInTheDocument();
    expect(within(row).getByText("AI 42")).toBeInTheDocument();
    expect(within(row).getByText("manual 40")).toBeInTheDocument();
    expect(within(row).getByText("variance +2")).toBeInTheDocument();
  });

  it("shows the reason and note on a sign-off", async () => {
    server.use(
      http.get("/api/v1/audit", () =>
        HttpResponse.json([
          entry({
            action: "session_approved",
            actor: "admin",
            detail: { reason: "camera_blocked", variance: 12, note: "forklift in view" },
          }),
        ]),
      ),
    );
    renderPage(<Audit />, { path: "/audit", route: "/audit" });

    // scope to the row: the action labels also appear in the filter dropdown
    const row = (await screen.findByText("ABC 1234")).closest("li") as HTMLElement;
    expect(within(row).getByText("Signed off a dispute")).toBeInTheDocument();
    expect(within(row).getByText("camera blocked")).toBeInTheDocument();
    expect(within(row).getByText(/forklift in view/)).toBeInTheDocument();
  });

  it("asks the API for the chosen window and action", async () => {
    const seen = vi.fn();
    server.use(
      http.get("/api/v1/audit", ({ request }) => {
        seen(Object.fromEntries(new URL(request.url).searchParams));
        return HttpResponse.json([entry()]);
      }),
    );
    renderPage(<Audit />, { path: "/audit", route: "/audit" });
    await screen.findByText("ABC 1234");

    await userEvent.click(screen.getByRole("button", { name: "30 days" }));
    await userEvent.selectOptions(screen.getByLabelText("Action"), "session_approved");

    expect(seen).toHaveBeenLastCalledWith(
      expect.objectContaining({ days: "30", action: "session_approved" }),
    );
  });

  it("says so when the window is empty", async () => {
    server.use(http.get("/api/v1/audit", () => HttpResponse.json([])));
    renderPage(<Audit />, { path: "/audit", route: "/audit" });

    expect(await screen.findByText("Nothing recorded in this window")).toBeInTheDocument();
  });
});
