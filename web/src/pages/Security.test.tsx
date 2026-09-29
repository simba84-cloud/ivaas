import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import type { Incident, SecurityStatus, Zone } from "../api/types";
import type { Me } from "../auth/session";
import { bay, camera, site, meAs } from "../test/fixtures";
import { renderPage } from "../test/render";
import { server } from "../test/server";
import Security from "./Security";

const ADMIN: Me = meAs(["admin", "operator", "viewer"]);
const VIEWER: Me = meAs(["viewer"]);
const CAM = camera({ id: "cam-1", name: "Yard 1", role: "overhead", status: "online" });

const QUIET: SecurityStatus = {
  face_recognition: false,
  face_models_installed: false,
  enrolled_people: 0,
  badge_events_24h: 0,
  last_badge_at: null,
  edge: null,
};

const incident = (over: Partial<Incident> = {}): Incident => ({
  id: "i1",
  bay_id: bay.id,
  camera_id: "cam-1",
  kind: "intrusion",
  zone_id: "z1",
  zone_name: "Apron",
  detected_at: "2026-09-28T23:10:00Z",
  confidence: 0.91,
  snapshot_url: "/api/v1/objects/incidents/i1.jpg?exp=1&sig=x",
  detail: {},
  status: "open",
  acknowledged_by: null,
  acknowledged_at: null,
  resolved_by: null,
  resolved_at: null,
  resolution_note: null,
  ...over,
});

function api({
  status = QUIET,
  incidents = [incident()] as Incident[],
  zones = [] as Zone[],
} = {}) {
  const posted: { path: string; body: unknown }[] = [];
  server.use(
    http.get("/api/v1/sites", () => HttpResponse.json([site])),
    http.get("/api/v1/bays", () => HttpResponse.json([bay])),
    http.get(`/api/v1/bays/${bay.id}/cameras`, () => HttpResponse.json([CAM])),
    http.get(`/api/v1/bays/${bay.id}/zones`, () => HttpResponse.json(zones)),
    http.get("/api/v1/security/status", () => HttpResponse.json(status)),
    http.get("/api/v1/incidents", () => HttpResponse.json(incidents)),
    http.get("/api/v1/cameras/cam-1/snapshot", () => new HttpResponse(null, { status: 404 })),
    http.get("/api/v1/sessions", () => HttpResponse.json([])),
    http.post("/api/v1/incidents/i1/acknowledge", () => {
      posted.push({ path: "ack", body: null });
      return HttpResponse.json(incident({ status: "acknowledged", acknowledged_by: "admin" }));
    }),
    http.post("/api/v1/cameras/cam-1/zones", async ({ request }) => {
      const body = await request.json();
      posted.push({ path: "zone", body });
      return HttpResponse.json({ ...(body as object), id: "z9", camera_id: "cam-1", armed: true }, { status: 201 });
    }),
  );
  return posted;
}

const show = (me: Me = ADMIN, path = "/security") => renderPage(<Security me={me} />, { path, route: "/security" });

describe("security", () => {
  it("says plainly what is not being watched", async () => {
    api();
    show();
    const strip = await screen.findByRole("region", { name: "What is being watched" });
    expect(within(strip).getAllByText("The edge node has not checked in").length).toBeGreaterThan(0);
    expect(await within(strip).findByText(/Off\. An admin records the legal basis/)).toBeInTheDocument();
    expect(within(strip).getByText(/No swipes received/)).toBeInTheDocument();
  });

  it("names what the edge node is running when it has checked in", async () => {
    api({
      status: { ...QUIET, edge: { reported_at: new Date().toISOString(), detectors: ["people"] } },
    });
    show();
    const strip = await screen.findByRole("region", { name: "What is being watched" });
    expect(await within(strip).findByText(/Detecting people/)).toBeInTheDocument();
    expect(within(strip).getByText(/No fire model installed/)).toBeInTheDocument();
  });

  it("shows an incident with its evidence and acknowledges it", async () => {
    const posted = api();
    show();
    const card = (await screen.findByRole("img", { name: /Snapshot: Person in a restricted zone in Apron/ })).closest("article")!;
    expect(within(card).getByText("91%")).toBeInTheDocument();
    expect(within(card).getByText("Critical")).toBeInTheDocument();
    await userEvent.click(within(card).getByRole("button", { name: /Acknowledge/ }));
    await waitFor(() => expect(posted.map((p) => p.path)).toEqual(["ack"]));
  });

  it("needs a note before an incident can be resolved", async () => {
    api();
    show();
    const card = (await screen.findByRole("img", { name: /Snapshot/ })).closest("article")!;
    await userEvent.click(within(card).getByRole("button", { name: /Resolve/ }));
    const resolve = within(card).getByRole("button", { name: "Resolve" });
    expect(resolve).toBeDisabled();
    await userEvent.type(within(card).getByLabelText("What was found"), "Guard on patrol");
    expect(resolve).toBeEnabled();
  });

  it("lets viewers see incidents but not act, and hides faces and the badge log", async () => {
    api();
    show(VIEWER);
    const card = (await screen.findByRole("img", { name: /Snapshot/ })).closest("article")!;
    expect(within(card).queryByRole("button", { name: /Acknowledge/ })).not.toBeInTheDocument();
    const tabs = screen.getByRole("group", { name: "Security section" });
    expect(within(tabs).queryByRole("button", { name: "Enrolled faces" })).not.toBeInTheDocument();
    expect(within(tabs).queryByRole("button", { name: "Badge log" })).not.toBeInTheDocument();
  });

  it("lets an admin draw a zone on the camera and save it", async () => {
    const posted = api({ incidents: [] });
    show(ADMIN, "/security?tab=zones");
    expect(await screen.findByText("Not streaming: drawing on a blank frame")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /New zone/ }));

    const view = screen.getByLabelText("Zones on Yard 1");
    view.getBoundingClientRect = () => ({ left: 0, top: 0, width: 1000, height: 1000 }) as DOMRect;
    for (const [x, y] of [[100, 100], [500, 100], [500, 500]]) fireEvent.click(view, { clientX: x, clientY: y });

    await userEvent.type(screen.getByLabelText("Name"), "Apron");
    await userEvent.click(screen.getByRole("button", { name: "Save zone" }));
    await waitFor(() => expect(posted[0]?.path).toBe("zone"));
    expect(posted[0].body).toMatchObject({
      name: "Apron",
      polygon: [[0.1, 0.1], [0.5, 0.1], [0.5, 0.5]],
      rules: ["intrusion"],
      schedule: [],
    });
  });

  it("marks rules inactive when nothing can check them", async () => {
    api({ incidents: [] });
    show(ADMIN, "/security?tab=zones");
    await userEvent.click(await screen.findByRole("button", { name: /New zone/ }));
    expect(screen.getByText("Inactive: face recognition is switched off")).toBeInTheDocument();
    expect(screen.getByText("Inactive: needs a fire model trained on this site")).toBeInTheDocument();
  });

  it("keeps enrolment locked while face recognition is off", async () => {
    server.use(http.get("/api/v1/people", () => HttpResponse.json([])));
    api();
    show(ADMIN, "/security?tab=people");
    expect(await screen.findByText("Face recognition is switched off")).toBeInTheDocument();
    expect(screen.queryByLabelText("Photo (front-on, one face)")).not.toBeInTheDocument();
  });
});
