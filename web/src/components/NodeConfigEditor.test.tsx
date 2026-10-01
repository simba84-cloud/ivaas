import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";
import EdgeNodes from "../pages/EdgeNodes";
import { bay, camera, edgeNode, meAs, site } from "../test/fixtures";
import { renderPage } from "../test/render";
import { server } from "../test/server";

const choke = camera({ id: "c1", name: "Chokepoint 1", role: "chokepoint" });
const overhead = camera({ id: "c2", name: "Overhead 1", role: "overhead" });
const lpr = camera({ id: "c3", name: "LPR 1", role: "lpr" });

function stack(node = edgeNode({ config: {} }), snapshot: Blob | null = null) {
  let saved: unknown = null;
  server.use(
    http.get("/api/v1/sites", () => HttpResponse.json([site])),
    http.get("/api/v1/bays", () => HttpResponse.json([bay])),
    http.get("/api/v1/edge/nodes", () => HttpResponse.json([node])),
    http.get(`/api/v1/bays/${bay.id}/cameras`, () => HttpResponse.json([choke, overhead, lpr])),
    http.get("/api/v1/cameras/:id/snapshot", () =>
      snapshot
        ? new HttpResponse(snapshot, { headers: { "content-type": "image/jpeg" } })
        : HttpResponse.json({ detail: "the camera is not streaming" }, { status: 404 }),
    ),
    http.put(`/api/v1/edge/nodes/${node.id}/config`, async ({ request }) => {
      saved = await request.json();
      return HttpResponse.json(node);
    }),
  );
  renderPage(<EdgeNodes me={meAs(["admin"])} />, { path: "/edge", route: "/edge" });
  return () => saved;
}

const open = async () => userEvent.click(await screen.findByRole("button", { name: "Configure" }));
const rowOf = (name: string) => screen.getByText(name, { selector: "div.font-semibold" }).closest("tr")!;

afterEach(() => vi.restoreAllMocks());

describe("node configuration editor", () => {
  it("configures a new node: a line for the chokepoint, a zone elsewhere, plates for the LPR", async () => {
    const saved = stack();
    await open();
    expect(await screen.findByText("What Loading bay edge counts")).toBeInTheDocument();
    // a node with nothing configured starts with every camera on, nothing drawn
    const save = screen.getByRole("button", { name: "Save configuration" });
    expect(save).toBeDisabled();
    expect(screen.getByText("Draw the counting line for Chokepoint 1")).toBeInTheDocument();

    // the chokepoint has no frame to draw on: its coordinates are typed
    await userEvent.click(within(rowOf("Chokepoint 1")).getByRole("button", { name: /Draw/ }));
    expect(await screen.findByText(/not streaming, so there is no frame/)).toBeInTheDocument();
    for (const [label, value] of [
      ["from x", "640"],
      ["to x", "640"],
    ] as const) {
      await userEvent.type(screen.getByLabelText(label), value);
    }
    const ys = screen.getAllByLabelText("y");
    await userEvent.type(ys[0], "0");
    await userEvent.type(ys[1], "720");
    await userEvent.click(screen.getByRole("button", { name: "Done" }));

    await userEvent.click(within(rowOf("Overhead 1")).getByRole("button", { name: /Draw/ }));
    for (const [label, value] of [
      ["left", "0"],
      ["top", "0"],
      ["right", "1280"],
      ["bottom", "720"],
    ] as const) {
      await userEvent.type(await screen.findByLabelText(label), value);
    }
    expect(save).toBeEnabled();
    await userEvent.click(save);
    await vi.waitFor(() => expect(saved()).not.toBeNull());
    expect(saved()).toEqual({
      model: { path: "/models/stacks-v2.onnx", arch: "rtdetr" },
      layers_model: "/models/layers-v3.onnx",
      cameras: [
        { api_camera_id: "c1", stride: 1, line: [[640, 0], [640, 720]] },
        { api_camera_id: "c2", stride: 2, zone: [0, 0, 1280, 720] },
      ],
      lpr_cameras: [{ api_camera_id: "c3", stride: 5 }],
    });
  });

  it("draws a line on a frame in the camera's own pixels", async () => {
    // jsdom has no object URLs; the browser makes one per frame
    URL.createObjectURL = vi.fn(() => "blob:frame");
    URL.revokeObjectURL = vi.fn();
    stack(edgeNode({ config: {} }), new Blob([new Uint8Array([0xff, 0xd8])], { type: "image/jpeg" }));
    await open();
    await screen.findByText("Chokepoint 1", { selector: "div.font-semibold" });
    await userEvent.click(within(rowOf("Chokepoint 1")).getByRole("button", { name: /Draw/ }));
    const img = await screen.findByAltText("Still frame from Chokepoint 1");
    Object.defineProperty(img, "naturalWidth", { value: 1920 });
    Object.defineProperty(img, "naturalHeight", { value: 1080 });
    fireEvent.load(img);
    const surface = await screen.findByRole("img", { name: "Drawing surface for Chokepoint 1" });
    // shown at half size: a click at screen (480, 0) is pixel (960, 0) of the camera
    surface.getBoundingClientRect = () => ({ left: 0, top: 0, width: 960, height: 540 }) as DOMRect;
    fireEvent.click(surface, { clientX: 480, clientY: 0 });
    fireEvent.click(surface, { clientX: 480, clientY: 540 });
    expect(within(rowOf("Chokepoint 1")).getByText("[[960,0],[960,1080]]")).toBeInTheDocument();
    expect(screen.getByText(/The frame is 1920 × 1080 pixels/)).toBeInTheDocument();
  });

  it("keeps what the node runs, warns about a slow chokepoint, and saves only what changed", async () => {
    const saved = stack(
      edgeNode({
        config: {
          model: { path: "/models/stacks-v3.onnx", arch: "rtdetr" },
          layers_model: "/models/layers-v3.onnx",
          forward_means: "loading",
          cameras: [{ api_camera_id: "c1", key: "door", line: [[10, 0], [10, 99]], stride: 1, frames: "latest" }],
          lpr_cameras: [{ api_camera_id: "c3", stride: 10 }],
        },
      }),
    );
    await open();
    expect(await screen.findByText(/Model: \/models\/stacks-v3.onnx/)).toBeInTheDocument();
    // configured: the overhead camera it does not use stays off
    expect(within(rowOf("Overhead 1")).getByRole("checkbox")).not.toBeChecked();
    const stride = within(rowOf("Chokepoint 1")).getByLabelText("Stride for Chokepoint 1");
    await userEvent.selectOptions(stride, "3");
    expect(screen.getByText("Above 1, a chokepoint misses crossings.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save configuration" }));
    await vi.waitFor(() => expect(saved()).not.toBeNull());
    expect(saved()).toEqual({
      model: { path: "/models/stacks-v3.onnx", arch: "rtdetr" },
      layers_model: "/models/layers-v3.onnx",
      forward_means: "loading",
      cameras: [{ api_camera_id: "c1", key: "door", frames: "latest", stride: 3, line: [[10, 0], [10, 99]] }],
      lpr_cameras: [{ api_camera_id: "c3", stride: 10 }],
    });
  });

  it("is offered only to those who may calibrate", async () => {
    server.use(
      http.get("/api/v1/sites", () => HttpResponse.json([site])),
      http.get("/api/v1/bays", () => HttpResponse.json([bay])),
      http.get("/api/v1/edge/nodes", () => HttpResponse.json([edgeNode()])),
    );
    renderPage(<EdgeNodes me={meAs(["viewer"])} />, { path: "/edge", route: "/edge" });
    await screen.findByText("Loading bay edge");
    expect(screen.queryByRole("button", { name: "Configure" })).not.toBeInTheDocument();
  });
});
