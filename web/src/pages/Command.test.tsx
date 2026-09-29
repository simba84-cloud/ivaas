import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { emitLive } from "../api/live";
import type { Camera, Session } from "../api/types";
import { bay, camera, overview, session, site } from "../test/fixtures";
import { renderPage } from "../test/render";
import { server } from "../test/server";
import Command from "./Command";

const CAMS = [
  camera({ id: "1", name: "Chokepoint 1", role: "chokepoint", status: "online" }),
  camera({ id: "2", name: "Overhead 1", role: "overhead", status: "offline" }),
];
const OPEN = session({ id: "open", status: "open", plate: "ABC 1234", ai_count: 42, closed_at: null });

function api({
  cameras = CAMS,
  sessions = [OPEN] as Session[],
  view = overview(),
}: { cameras?: Camera[]; sessions?: Session[]; view?: ReturnType<typeof overview> } = {}) {
  server.use(
    http.get("/api/v1/sites", () => HttpResponse.json([site])),
    http.get("/api/v1/bays", () => HttpResponse.json([bay])),
    http.get(`/api/v1/bays/${bay.id}/cameras`, () => HttpResponse.json(cameras)),
    http.get("/api/v1/sessions", () => HttpResponse.json(sessions)),
    http.get("/api/v1/analytics/overview", () => HttpResponse.json(view)),
    http.get("http://localhost:8889/", () => HttpResponse.json({})),
    http.get("/api/v1/alerts/acknowledgements", () => HttpResponse.json([])),
  );
}

const show = () => renderPage(<Command />, { path: "/command", route: "/command" });

describe("command view", () => {
  it("shows the open load's count and plate over the feed", async () => {
    api();
    show();
    const count = await screen.findByRole("status");
    expect(within(count).getByText("42")).toBeInTheDocument();
    expect(screen.getAllByText("ABC 1234").length).toBeGreaterThan(0);
  });

  it("raises an offline camera as needing attention", async () => {
    api();
    show();
    expect(await screen.findByText("Overhead 1 has no signal")).toBeInTheDocument();
  });

  it("switches the main view to the camera chosen on the wall", async () => {
    api();
    show();
    const tile = await screen.findByRole("button", { name: "Show Overhead 1 in the main view" });
    await userEvent.click(tile);
    expect(tile).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("Overhead 1 · Overhead")).toBeInTheDocument();
  });

  it("reports counts from the live stream as they arrive", async () => {
    api();
    show();
    await screen.findByRole("status");
    act(() => {
      emitLive({
        subject: "ivaas.session.updated",
        data: { id: "open", bay_id: bay.id, plate: "ABC 1234", ai_count: 51 },
        at: Date.now(),
      });
    });
    expect(await screen.findByText("+9 crates counted")).toBeInTheDocument();
  });

  it("does not invent accuracy before any load is verified", async () => {
    api({ view: overview({ mean_accuracy: null, verified_sessions: 0 }) });
    show();
    expect(await screen.findByText("Awaiting the first manual verification")).toBeInTheDocument();
    expect(screen.getByText("No load has a manual count yet")).toBeInTheDocument();
  });

  it("says so when the bay has no cameras, rather than showing it healthy", async () => {
    api({ cameras: [], sessions: [], view: overview({ cameras_online: 0, cameras_total: 0 }) });
    show();
    expect(await screen.findAllByText("No cameras registered")).not.toHaveLength(0);
    expect(screen.getByText("Bay clear · no session open")).toBeInTheDocument();
    expect(screen.queryByText("Nothing needs attention at this bay.")).not.toBeInTheDocument();
  });
});
