import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { bay, site } from "../test/fixtures";
import { renderPage } from "../test/render";
import { server } from "../test/server";
import { useScope } from "./scope";

const SECOND_SITE = { id: "s2", name: "Second Plant", timezone: "UTC" };
const SECOND_BAY = { ...bay, id: "b2", site_id: "s2", name: "Bay 2" };

/** A probe that renders whatever the scope currently points at. */
function Probe() {
  const { bay: current, site: currentSite, bays, multi, setBay } = useScope();
  return (
    <div>
      <p data-testid="current">
        {currentSite?.name} / {current?.name}
      </p>
      <p data-testid="multi">{String(multi)}</p>
      {bays.map((b) => (
        <button key={b.id} onClick={() => setBay(b.id)}>
          go {b.name}
        </button>
      ))}
    </div>
  );
}

function api(sites: unknown[], bays: unknown[]) {
  server.use(
    http.get("/api/v1/sites", () => HttpResponse.json(sites)),
    http.get("/api/v1/bays", () => HttpResponse.json(bays)),
  );
}

describe("bay scope", () => {
  it("picks the only bay and does not offer a choice", async () => {
    api([site], [bay]);
    renderPage(<Probe />);

    // the probe renders before the queries land, and sites/bays resolve separately
    await waitFor(() =>
      expect(screen.getByTestId("current")).toHaveTextContent(
        "Bakery Industrial Site / Loading Bay",
      ),
    );
    expect(screen.getByTestId("multi")).toHaveTextContent("false");
  });

  it("offers a choice once a second site exists, and switching sticks", async () => {
    api([site, SECOND_SITE], [bay, SECOND_BAY]);
    renderPage(<Probe />);

    await screen.findByRole("button", { name: "go Bay 2" });
    expect(screen.getByTestId("multi")).toHaveTextContent("true");
    await userEvent.click(screen.getByRole("button", { name: "go Bay 2" }));

    await waitFor(() =>
      expect(screen.getByTestId("current")).toHaveTextContent("Second Plant / Bay 2"),
    );
    expect(localStorage.getItem("ivaas.bay")).toBe("b2");
  });

  it("falls back to a real bay when the remembered one has gone", async () => {
    localStorage.setItem("ivaas.bay", "deleted-bay");
    api([site], [bay]);
    renderPage(<Probe />);

    // a stale id must not strand the portal on an empty screen
    await screen.findByRole("button", { name: "go Loading Bay" });
    expect(screen.getByTestId("current")).toHaveTextContent("Loading Bay");
  });
});
