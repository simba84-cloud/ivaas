import { describe, expect, it } from "vitest";
import { bay, camera, edgeNode, session } from "../test/fixtures";
import { type Known, attention, history, interpret, mergeActivity } from "./activity";

const msg = (subject: string, data: Record<string, unknown>, at = 1_000) => ({ subject, data, at });

describe("live events", () => {
  it("turns a running total into the crates just counted", () => {
    const known = new Map<string, Known>([["s1", { plate: "ABC 1234", ai_count: 40 }]]);
    const r = interpret(msg("ivaas.session.updated", { id: "s1", bay_id: bay.id, plate: "ABC 1234", ai_count: 49 }), known, bay.id);

    expect(r.items.map((i) => i.title)).toEqual(["+9 crates counted"]);
    expect(r.marks).toMatchObject([{ kind: "crates", label: "+9" }]);
    expect(known.get("s1")?.ai_count).toBe(49);
  });

  it("does not claim crates for a session it has not seen before", () => {
    const r = interpret(msg("ivaas.session.updated", { id: "new", bay_id: bay.id, plate: null, ai_count: 30 }), new Map(), bay.id);
    expect(r.items).toEqual([]);
  });

  it("reports a plate the first time it is read", () => {
    const known = new Map<string, Known>([["s1", { plate: null, ai_count: 0 }]]);
    const r = interpret(msg("ivaas.session.updated", { id: "s1", bay_id: bay.id, plate: "XYZ 9876", ai_count: 0 }), known, bay.id);
    expect(r.items[0].title).toBe("Plate XYZ 9876 read");
    expect(r.marks[0]).toMatchObject({ kind: "plate", label: "XYZ 9876" });
  });

  it("ignores events from another bay", () => {
    const r = interpret(msg("ivaas.session.opened", { id: "s9", bay_id: "other" }), new Map(), bay.id);
    expect(r.items).toEqual([]);
  });

  it("says an approval leaves the counts alone", () => {
    const r = interpret(msg("ivaas.session.approved", { id: "s1", bay_id: bay.id, plate: "ABC 1234", ai_count: 42 }), new Map(), bay.id);
    expect(r.items[0].detail).toMatch(/counts are unchanged/);
  });

  it("skips analysis progress and reports the outcome", () => {
    expect(interpret(msg("ivaas.analysis.updated", { id: "j", status: "running" }), new Map(), bay.id).items).toEqual([]);
    const done = interpret(msg("ivaas.analysis.updated", { id: "j", status: "done", filename: "a.mp4", total_crates: 16 }), new Map(), bay.id);
    expect(done.items[0].detail).toBe("a.mp4: 16 crates counted.");
  });
});

describe("attention", () => {
  it("does not show an all-clear when no cameras exist", () => {
    const items = attention({ cameras: [], mediaUp: true, sessions: [], insights: [] });
    expect(items.map((i) => i.title)).toEqual(["No cameras registered"]);
  });

  it("puts faults first and links them to the camera", () => {
    const items = attention({
      cameras: [camera({ id: "a", name: "Door 1", status: "offline" }), camera({ id: "b", status: "online" })],
      mediaUp: true,
      sessions: [session({ id: "d", status: "disputed", manual_count: 45, variance: -3 }), session({ id: "c", status: "closed" })],
      insights: [],
    });
    expect(items[0]).toMatchObject({ severity: "bad", title: "Door 1 has no signal", cameraId: "a" });
    expect(items.map((i) => i.title)).toContain("Load ABC 1234 disputed");
    expect(items.map((i) => i.title)).toContain("1 load waiting for a manual count");
  });
});

describe("activity", () => {
  it("does not repeat a close the live stream already reported", () => {
    const stored = history([session({ id: "s1", status: "closed" })]);
    const live = interpret(msg("ivaas.session.closed", { id: "s1", bay_id: bay.id, plate: "ABC 1234", ai_count: 42 }, Date.now()), new Map(), bay.id).items;
    const merged = mergeActivity(live, stored);
    expect(merged.filter((i) => i.title.startsWith("Load closed"))).toHaveLength(1);
  });
});

describe("edge node alerts", () => {
  const base = { cameras: [camera({ status: "online" })], mediaUp: true, sessions: [], insights: [] };

  it("raises nothing for a healthy node", () => {
    expect(attention({ ...base, nodes: [edgeNode()] })).toEqual([]);
  });

  it("calls a silent node offline, with when it last reported", () => {
    const [item] = attention({ ...base, nodes: [edgeNode({ health: "offline" })] });
    expect(item).toMatchObject({ severity: "bad", title: "Edge node Loading bay edge is offline" });
    expect(item.detail).toMatch(/queued on the node/);
  });

  it("never treats a node that has not reported as healthy", () => {
    const [item] = attention({
      ...base,
      nodes: [edgeNode({ health: "never_seen", last_seen_at: null, cameras: [] })],
    });
    expect(item).toMatchObject({ severity: "warn", title: "Edge node Loading bay edge has never reported" });
  });

  it("names the camera a running node cannot read", () => {
    const node = edgeNode({
      cameras: [{ api_camera_id: "c1", name: "Chokepoint 1", connected: false, fps: 0, lag_s: null }],
    });
    const [item] = attention({ ...base, nodes: [node] });
    expect(item).toMatchObject({ severity: "bad", title: "Loading bay edge cannot read Chokepoint 1", cameraId: "c1" });
  });

  it("says nothing while nodes are still unknown, and nothing about revoked ones", () => {
    expect(attention({ ...base, nodes: undefined })).toEqual([]);
    expect(attention({ ...base, nodes: [edgeNode({ health: "revoked", status: "revoked" })] })).toEqual([]);
  });
});
