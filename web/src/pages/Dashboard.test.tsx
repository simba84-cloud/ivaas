import { screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { bay, camera, overview, session } from "../test/fixtures";
import { renderPage } from "../test/render";
import { server } from "../test/server";
import Dashboard from "./Dashboard";

const me = (roles: string[]) => ({ subject: "u", name: "u", roles });

function api({
  over = overview(),
  cameras = [camera({ status: "online" })],
  sessions = [session()],
} = {}) {
  server.use(
    http.get("/api/v1/analytics/overview", () => HttpResponse.json(over)),
    http.get("/api/v1/bays", () => HttpResponse.json([bay])),
    http.get("/api/v1/sessions", () => HttpResponse.json(sessions)),
    http.get(`/api/v1/bays/${bay.id}/cameras`, () => HttpResponse.json(cameras)),
  );
}

/** The card is the nearest article; assert the value sits in the same one as the label. */
const card = (label: string) => screen.getByText(label).closest("article") as HTMLElement;

describe("operations dashboard", () => {
  it("shows each KPI with its value and its change on the prior period", async () => {
    api();
    renderPage(<Dashboard me={me(["admin"])} />);

    await screen.findByText("+18%"); // wait for the overview query to land
    expect(within(card("Crates today")).getByText("120")).toBeInTheDocument();
    expect(within(card("Crates today")).getByText("+18%")).toBeInTheDocument();

    // accuracy moves in points, not percent, so it must not be shown as "+1%"
    expect(within(card("Counting accuracy")).getByText("96.2%")).toBeInTheDocument();
    expect(within(card("Counting accuracy")).getByText("+1.2 pts")).toBeInTheDocument();

    expect(within(card("Camera health")).getByText("2/4")).toBeInTheDocument();
  });

  it("says so rather than showing 0% when there is no prior period", async () => {
    api({ over: overview({ crates: { value: 10, delta_pct: null, series: [10] } }) });
    renderPage(<Dashboard me={me(["admin"])} />);

    expect(
      await within(card("Crates today")).findByText("No prior period to compare"),
    ).toBeInTheDocument();
  });

  it("renders the insight feed from the API and counts what needs action", async () => {
    api();
    renderPage(<Dashboard me={me(["admin"])} />);

    expect(await screen.findByText("2 of 4 cameras are not streaming")).toBeInTheDocument();
    expect(screen.getByText(/Door 3, Door 4/)).toBeInTheDocument();
    expect(screen.getByText("1 to action")).toBeInTheDocument(); // one warn, one good
  });

  it("shows the live count over the feed while a truck is at the bay", async () => {
    api({ sessions: [session({ status: "open", ai_count: 37, plate: "ABC 1234" })] });
    renderPage(<Dashboard me={me(["operator"])} />);

    const live = await screen.findByRole("status");
    expect(live).toHaveAccessibleName("Crates counted: 37");
    expect(within(live).getByText("37")).toBeInTheDocument();
    // the plate shows over the feed as well as in the recent-trucks list
    expect(screen.getAllByText("ABC 1234")).toHaveLength(2);
    expect(await screen.findByText("LIVE")).toBeInTheDocument(); // cameras query lands separately
    expect(screen.getByRole("button", { name: "End session" })).toBeEnabled();
  });

  it("marks the feed offline and offers to start a session when the bay is clear", async () => {
    api({ cameras: [camera({ status: "offline" })], sessions: [] });
    renderPage(<Dashboard me={me(["operator"])} />);

    expect(await screen.findByText(/No signal from the bay camera/)).toBeInTheDocument();
    expect(screen.getByText("OFFLINE")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start loading" })).toBeEnabled();
  });

  it("does not let a viewer operate the bay", async () => {
    api({ sessions: [] });
    renderPage(<Dashboard me={me(["viewer"])} />);

    expect(await screen.findByRole("button", { name: "Start loading" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Start offloading" })).toBeDisabled();
  });
});
