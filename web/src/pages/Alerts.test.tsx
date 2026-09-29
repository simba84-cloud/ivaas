import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import type { Acknowledgement } from "../api/types";
import type { Me } from "../auth/session";
import { bay, camera, overview, session, site } from "../test/fixtures";
import { renderPage } from "../test/render";
import { server } from "../test/server";
import Alerts from "./Alerts";

const OPERATOR: Me = { subject: "op", name: "Operator", roles: ["operator"] };
const VIEWER: Me = { subject: "v", name: "Viewer", roles: ["viewer"] };
const OFFLINE_KEY = "cam-2-offline@never";

function api({ acks = [] as Acknowledgement[], ackStatus = 200 } = {}) {
  const posted: unknown[] = [];
  const stored = [...acks]; // the server remembers what it accepted
  server.use(
    http.get("/api/v1/sites", () => HttpResponse.json([site])),
    http.get("/api/v1/bays", () => HttpResponse.json([bay])),
    http.get(`/api/v1/bays/${bay.id}/cameras`, () =>
      HttpResponse.json([
        camera({ id: "1", name: "Chokepoint 1", status: "online" }),
        camera({ id: "2", name: "Door 2", status: "offline" }),
      ]),
    ),
    http.get("/api/v1/sessions", () =>
      HttpResponse.json([session({ id: "d1", status: "disputed", plate: "XYZ 9", manual_count: 40, variance: 2 })]),
    ),
    http.get("/api/v1/analytics/overview", () => HttpResponse.json(overview({ insights: [] }))),
    http.get("http://localhost:8889/", () => HttpResponse.json({})),
    http.get("/api/v1/alerts/acknowledgements", () => HttpResponse.json(stored)),
    http.post("/api/v1/alerts/acknowledgements", async ({ request }) => {
      const body = (await request.json()) as { key: string };
      posted.push(body);
      if (ackStatus !== 200) return HttpResponse.json({ detail: "refused" }, { status: ackStatus });
      const ack = { key: body.key, acknowledged_by: "op", acknowledged_at: new Date().toISOString(), note: null };
      stored.push(ack);
      return HttpResponse.json(ack);
    }),
  );
  return posted;
}

const show = (me: Me = OPERATOR) => renderPage(<Alerts me={me} />, { path: "/alerts", route: "/alerts" });
// scoped to the alert list: a toast can repeat an alert's title
const card = (title: string) =>
  within(screen.getByRole("region", { name: "Active alerts" })).getByText(title).closest("li") as HTMLElement;

describe("alerts", () => {
  it("lists current faults first, each named by severity", async () => {
    api();
    show();
    await screen.findByText("Door 2 has no signal");
    const titles = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(titles[0]).toMatch(/Door 2 has no signal.*Critical/);
    expect(within(card("Load XYZ 9 disputed")).getByText("Warning")).toBeInTheDocument();
  });

  it("acknowledges an alert, records the occurrence key, and moves it aside", async () => {
    const posted = api();
    show();
    await screen.findByText("Door 2 has no signal");
    await userEvent.click(within(card("Door 2 has no signal")).getByRole("button", { name: "Acknowledge" }));

    await waitFor(() => expect(posted).toEqual([{ key: OFFLINE_KEY, title: "Door 2 has no signal", note: null }]));
    expect(await screen.findByRole("status")).toHaveTextContent("Acknowledged");
    await userEvent.click(screen.getByRole("button", { name: /Acknowledged/ }));
    await waitFor(() => expect(within(card("Door 2 has no signal")).getByText(/op/)).toBeInTheDocument());
  });

  it("puts the alert back if the server refuses", async () => {
    api({ ackStatus: 500 });
    show();
    await screen.findByText("Door 2 has no signal");
    await userEvent.click(within(card("Door 2 has no signal")).getByRole("button", { name: "Acknowledge" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Not acknowledged");
    await waitFor(() =>
      expect(within(card("Door 2 has no signal")).getByRole("button", { name: "Acknowledge" })).toBeInTheDocument(),
    );
  });

  it("filters by severity", async () => {
    api();
    show();
    await screen.findByText("Door 2 has no signal");
    await userEvent.click(within(screen.getByRole("group", { name: "Severity" })).getByRole("button", { name: /Warning/ }));
    await waitFor(() => expect(screen.queryByText("Door 2 has no signal")).not.toBeInTheDocument());
    expect(screen.getByText("Load XYZ 9 disputed")).toBeInTheDocument();
  });

  it("lets viewers read alerts but not acknowledge them", async () => {
    api();
    show(VIEWER);
    await screen.findByText("Door 2 has no signal");
    expect(screen.queryByRole("button", { name: "Acknowledge" })).not.toBeInTheDocument();
    expect(screen.getByText("Operators and admins can acknowledge alerts.")).toBeInTheDocument();
  });

  it("keeps an acknowledged alert out of the active list", async () => {
    api({ acks: [{ key: OFFLINE_KEY, acknowledged_by: "admin", acknowledged_at: "2026-09-28T08:00:00Z", note: "Electrician called" }] });
    show();
    await screen.findByText("Load XYZ 9 disputed");
    expect(screen.queryByText("Door 2 has no signal")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Acknowledged/ }));
    expect(await screen.findByText(/Electrician called/)).toBeInTheDocument();
  });
});
