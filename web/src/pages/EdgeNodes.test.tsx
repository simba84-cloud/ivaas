import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";
import { bay, edgeNode, meAs, site } from "../test/fixtures";
import { renderPage } from "../test/render";
import { server } from "../test/server";
import EdgeNodes from "./EdgeNodes";

const api = (nodes = [edgeNode()]) =>
  server.use(
    http.get("/api/v1/sites", () => HttpResponse.json([site])),
    http.get("/api/v1/bays", () => HttpResponse.json([bay])),
    http.get("/api/v1/edge/nodes", () => HttpResponse.json(nodes)),
  );

const show = (who = "admin") =>
  renderPage(<EdgeNodes me={meAs([who])} />, { path: "/edge", route: "/edge" });

describe("edge nodes", () => {
  it("shows each node's health, cameras and backlog from its heartbeats", async () => {
    api([
      edgeNode({
        spool_pending: 12,
        cameras: [
          { api_camera_id: "c1", name: "Chokepoint 1", connected: true, fps: 7, lag_s: 0.1 },
          { api_camera_id: "c2", name: "LPR 1", connected: false, fps: 0, lag_s: null },
        ],
      }),
    ]);
    show();
    const row = (await screen.findByText("Loading bay edge")).closest("tr") as HTMLElement;
    expect(within(row).getByText("Online")).toBeInTheDocument();
    expect(within(row).getByText("1 of 2 streaming")).toBeInTheDocument();
    expect(within(row).getByText("LPR 1: no stream")).toBeInTheDocument();
    expect(within(row).getByText("12 queued")).toBeInTheDocument();
  });

  it("never shows a node that has not reported as online", async () => {
    api([edgeNode({ health: "never_seen", last_seen_at: null, cameras: [], spool_pending: null })]);
    show();
    const row = (await screen.findByText("Loading bay edge")).closest("tr") as HTMLElement;
    expect(within(row).getByText("Never reported")).toBeInTheDocument();
    expect(within(row).queryByText("Online")).not.toBeInTheDocument();
    expect(within(row).getByText("None reported")).toBeInTheDocument();
  });

  it("shows the enrollment token once, with the command to run", async () => {
    api([]);
    const created = vi.fn();
    server.use(
      http.post(`/api/v1/sites/${site.id}/enrollment-tokens`, async ({ request }) => {
        created(await request.json());
        return HttpResponse.json(
          { token: "ivaas-enr-abc.secret", name: "Bay edge", site_id: site.id, bay_id: bay.id, expires_at: "2026-10-01T09:00:00Z" },
          { status: 201 },
        );
      }),
    );
    show();
    expect(await screen.findByText("No edge nodes enrolled")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Add node/ }));
    await userEvent.type(screen.getByLabelText("Node name"), "Bay edge");
    await userEvent.click(screen.getByRole("button", { name: "Create token" }));

    expect(await screen.findByText(/--token ivaas-enr-abc.secret/)).toBeInTheDocument();
    expect(screen.getByText(/cannot be shown again/)).toBeInTheDocument();
    expect(created).toHaveBeenCalledWith({ name: "Bay edge", bay_id: bay.id, ttl_hours: 24 });
  });

  it("offers no management to people who cannot register devices", async () => {
    api();
    show("operator");
    await screen.findByText("Loading bay edge");
    expect(screen.queryByRole("button", { name: /Add node/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Revoke" })).not.toBeInTheDocument();
  });

  it("revokes a node", async () => {
    api();
    const revoked = vi.fn();
    server.use(
      http.delete("/api/v1/edge/nodes/n1", () => {
        revoked();
        return new HttpResponse(null, { status: 204 });
      }),
    );
    show();
    await userEvent.click(await screen.findByRole("button", { name: "Revoke" }));
    expect(revoked).toHaveBeenCalled();
  });
});

describe("edge node models", () => {
  it("shows the model running, a refused switch, and rolls back", async () => {
    api([
      edgeNode({
        models: { detector: { name: "stacks", version: "v1", sha256: "abc" } },
        model_error: "stacks v2: checksum mismatch; kept stacks v1",
        can_roll_back: true,
      }),
    ]);
    const rolled = vi.fn();
    server.use(
      http.post("/api/v1/edge/nodes/n1/rollback", () => {
        rolled();
        return HttpResponse.json(edgeNode());
      }),
    );
    show();
    const row = (await screen.findByText("Loading bay edge")).closest("tr") as HTMLElement;
    expect(within(row).getByText("Model: stacks v1")).toBeInTheDocument();
    expect(within(row).getByRole("alert")).toHaveTextContent(/checksum mismatch; kept stacks v1/);
    await userEvent.click(within(row).getByRole("button", { name: "Roll back" }));
    expect(rolled).toHaveBeenCalled();
  });

  it("offers no rollback without an earlier configuration, or to those who cannot calibrate", async () => {
    api([edgeNode({ can_roll_back: false })]);
    show();
    await screen.findByText("Loading bay edge");
    expect(screen.queryByRole("button", { name: "Roll back" })).not.toBeInTheDocument();
  });
});
