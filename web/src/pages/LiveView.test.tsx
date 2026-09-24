import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { bay, camera } from "../test/fixtures";
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

    await userEvent.click(screen.getByRole("button", { name: /Chokepoint/ }));
    expect(tiles()).toHaveLength(2);
  });

  it("can show only the cameras that need attention", async () => {
    api();
    renderPage(<LiveView />, { path: "/live", route: "/live" });
    await screen.findByText("1/4");

    await userEvent.click(screen.getByRole("button", { name: /Needs attention/ }));
    expect(tiles()).toHaveLength(3); // the one online camera drops out
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
});
