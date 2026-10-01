import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { bay, site } from "../test/fixtures";
import { renderPage } from "../test/render";
import { server } from "../test/server";
import Webhooks from "./Webhooks";

const hook = {
  id: "h1",
  url: "https://erp.example.com/ivaas",
  events: ["session.closed"],
  description: "Dispatch ERP",
  created_by: "admin",
  created_at: "2026-10-01T08:00:00Z",
};

describe("webhooks", () => {
  beforeEach(() =>
    server.use(
      http.get("/api/v1/sites", () => HttpResponse.json([site])),
      http.get("/api/v1/bays", () => HttpResponse.json([bay])),
    ),
  );

  it("shows the signing secret once, after creating an endpoint", async () => {
    let sent: unknown = null;
    let list: unknown[] = [];
    server.use(
      http.get("/api/v1/webhooks", () => HttpResponse.json(list)),
      http.post("/api/v1/webhooks", async ({ request }) => {
        sent = await request.json();
        list = [hook];
        return HttpResponse.json({ ...hook, secret: "whsec_c2VjcmV0" }, { status: 201 });
      }),
    );
    renderPage(<Webhooks />, { path: "/webhooks", route: "/webhooks" });
    expect(await screen.findByText("No webhooks")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Add webhook/ }));
    await userEvent.type(screen.getByLabelText("Receiver URL"), hook.url);
    await userEvent.click(screen.getByLabelText(/A manifest exception is raised/));
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByText("whsec_c2VjcmV0")).toBeInTheDocument();
    expect(sent).toEqual({ url: hook.url, events: ["session.closed", "exception.raised"], description: "" });
    expect(await screen.findByText(hook.url, { selector: "div" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText("whsec_c2VjcmV0")).not.toBeInTheDocument();
  });

  it("lists deliveries with their outcome and replays one", async () => {
    let replayed = "";
    server.use(
      http.get("/api/v1/webhooks", () => HttpResponse.json([hook])),
      http.get("/api/v1/webhooks/h1/deliveries", () =>
        HttpResponse.json([
          {
            id: "d1",
            event_id: "e1",
            event: "session.closed",
            status: "failed",
            attempts: 8,
            next_attempt_at: null,
            last_status_code: 500,
            last_error: "answered 500",
            delivered_at: null,
            replay_of: null,
            created_at: "2026-10-01T08:00:00Z",
          },
        ]),
      ),
      http.post("/api/v1/webhooks/deliveries/:id/replay", ({ params }) => {
        replayed = String(params.id);
        return HttpResponse.json({}, { status: 202 });
      }),
    );
    renderPage(<Webhooks />, { path: "/webhooks", route: "/webhooks" });
    await userEvent.click(await screen.findByRole("button", { name: "Deliveries" }));
    const row = (await screen.findByText("Gave up")).closest("tr")!;
    expect(within(row).getByText("Load closed")).toBeInTheDocument();
    expect(within(row).getByText(/500/)).toBeInTheDocument();
    await userEvent.click(within(row).getByRole("button", { name: /Replay/ }));
    await vi.waitFor(() => expect(replayed).toBe("d1"));
  });

  it("asks twice before removing an endpoint and its history", async () => {
    let removed = false;
    server.use(
      http.get("/api/v1/webhooks", () => HttpResponse.json(removed ? [] : [hook])),
      http.delete("/api/v1/webhooks/h1", () => {
        removed = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderPage(<Webhooks />, { path: "/webhooks", route: "/webhooks" });
    await userEvent.click(await screen.findByRole("button", { name: /Remove/ }));
    expect(removed).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: /Remove it and its history/ }));
    expect(await screen.findByText("No webhooks")).toBeInTheDocument();
  });
});
