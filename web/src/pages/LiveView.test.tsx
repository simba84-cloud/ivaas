import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { bay, camera, overview } from "../test/fixtures";
import { renderPage } from "../test/render";
import { server } from "../test/server";
import LiveView from "./LiveView";

const WALL = [
  camera({ id: "1", name: "Chokepoint 1", role: "chokepoint", status: "online" }),
  camera({ id: "2", name: "Chokepoint 2", role: "chokepoint", status: "offline" }),
  camera({ id: "3", name: "Overhead 1", role: "overhead", status: "offline" }),
  camera({ id: "4", name: "Lpr 1", role: "lpr", status: "degraded" }),
];

function api(cameras = WALL) {
  server.use(
    http.get("/api/v1/bays", () => HttpResponse.json([bay])),
    http.get(`/api/v1/bays/${bay.id}/cameras`, () => HttpResponse.json(cameras)),
    // the media-server probe is a no-cors fetch to another origin
    http.get("http://localhost:8889/", () => HttpResponse.json({})),
    http.get("/api/v1/sessions", () => HttpResponse.json([])),
    http.get("/api/v1/analytics/overview", () => HttpResponse.json(overview())),
    http.get("/api/v1/alerts/acknowledgements", () => HttpResponse.json([])),
  );
}

const tiles = () => screen.getAllByRole("figure");

describe("camera wall", () => {
  it("reports how many cameras are streaming", async () => {
    api();
    renderPage(<LiveView />, { path: "/live", route: "/live" });

    expect(await screen.findByText("1/4")).toBeInTheDocument();
    expect(screen.getByText(/cameras streaming/)).toBeInTheDocument();
  });

  it("labels each tile live or offline rather than inverting the whole tile", async () => {
    api();
    renderPage(<LiveView />, { path: "/live", route: "/live" });

    await screen.findByText("1/4");
    expect(within(tiles()[0]).getByText("LIVE")).toBeInTheDocument();
    expect(within(tiles()[1]).getByText("OFFLINE")).toBeInTheDocument();
    expect(within(tiles()[3]).getByText("DEGRADED")).toBeInTheDocument();
  });

  it("filters the wall by camera position", async () => {
    api();
    renderPage(<LiveView />, { path: "/live", route: "/live" });
    await screen.findByText("1/4");
    expect(tiles()).toHaveLength(4);

    const positions = screen.getByRole("group", { name: "Camera position" });
    await userEvent.click(within(positions).getByRole("button", { name: /Chokepoint/ }));
    await waitFor(() => expect(tiles()).toHaveLength(2));
  });

  it("can show only the cameras that need attention", async () => {
    api();
    renderPage(<LiveView />, { path: "/live", route: "/live" });
    await screen.findByText("1/4");

    await userEvent.click(screen.getByRole("button", { name: /Needs attention/ }));
    await waitFor(() => expect(tiles()).toHaveLength(3)); // the one online camera drops out
  });

  it("switches wall density", async () => {
    api();
    renderPage(<LiveView />, { path: "/live", route: "/live" });
    await screen.findByText("1/4");

    const compact = screen.getByRole("button", { name: "Compact" });
    await userEvent.click(compact);
    expect(compact).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Standard" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("says so when no cameras are registered", async () => {
    api([]);
    renderPage(<LiveView />, { path: "/live", route: "/live" });

    expect(await screen.findByText("No cameras registered")).toBeInTheDocument();
  });

  it("focuses a camera, with its details alongside, and Esc returns to the wall", async () => {
    api();
    renderPage(<LiveView />, { path: "/live", route: "/live" });
    await userEvent.click(await screen.findByRole("button", { name: "Focus Overhead 1" }));

    const panel = await screen.findByRole("region", { name: "Overhead 1, focused" });
    expect(within(panel).getByText("Not streaming")).toBeInTheDocument();
    expect(within(panel).getByText("never")).toBeInTheDocument(); // last frame
    await waitFor(() => expect(tiles()).toHaveLength(4)); // three on the wall, one focused

    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("region", { name: "Overhead 1, focused" })).not.toBeInTheDocument());
  });

  it("reorders the wall from the keyboard and remembers it", async () => {
    api();
    const first = renderPage(<LiveView />, { path: "/live", route: "/live" });
    const handle = await screen.findByRole("button", { name: /Move Chokepoint 1, position 1 of 4/ });
    handle.focus();
    await userEvent.keyboard("{ArrowRight}");

    await waitFor(() => expect(within(tiles()[1]).getByText("Chokepoint 1")).toBeInTheDocument());
    expect(screen.getByText("Chokepoint 1 moved to position 2 of 4")).toBeInTheDocument();

    first.unmount();
    renderPage(<LiveView />, { path: "/live", route: "/live" });
    await screen.findByText("1/4");
    expect(within(tiles()[1]).getByText("Chokepoint 1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Reset order/ })).toBeInTheDocument();
  });
});
